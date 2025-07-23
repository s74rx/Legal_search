import os
import json
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory
from werkzeug.utils import secure_filename
from sqlalchemy import text
import google.generativeai as genai
from dotenv import load_dotenv
import PyPDF2
from models import db, Citation
from setup_fts import setup_fts  # Import for auto-setup

# --- App Initialization ---
app = Flask(__name__)
load_dotenv()

# --- Configuration ---
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'supersecretkey')  # Use env var in production

# Production-ready database configuration
basedir = os.path.abspath(os.path.dirname(__file__))
if os.environ.get('RENDER'):
    # Production on Render - use absolute path for SQLite
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(basedir, "citations.db")}'
else:
    # Local development
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(basedir, "citations.db")}'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# Ensure directories exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Auto-run FTS setup on startup if needed (for initial deployment)
with app.app_context():
    db.create_all()
    # Check if FTS table exists; if not, run setup
    try:
        db.session.execute(text("SELECT 1 FROM citation_fts LIMIT 1;"))
    except:
        setup_fts()

# --- Database and Gemini API Initialization ---
gemini_api_key = os.getenv("GEMINI_API_KEY")
if not gemini_api_key:
    print("WARNING: GEMINI_API_KEY not set!")
    if os.environ.get('RENDER'):
        exit(1)  # Exit in production if API key missing
    else:
        try:
            genai.configure(api_key=gemini_api_key)
            print("Gemini API configured successfully")
        except Exception as e:
            print(f"Error configuring Gemini API: {e}")

# --- Helper Functions ---
def extract_text_from_pdf(pdf_path):
    """Enhanced PDF text extraction."""
    print(f"Attempting to read PDF at: {pdf_path}")
    try:
        with open(pdf_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            if len(reader.pages) == 0:
                print("Warning: PDF has no pages.")
                return None
            # Extract text from the first page only
            page = reader.pages[0]
            text = page.extract_text()
            print(f"Successfully extracted {len(text) if text else 0} characters from the first page.")
            return text if text and text.strip() else None
    except Exception as e:
        print(f"Error reading PDF: {e}")
        return None

def get_info_from_gemini(text):
    """Enhanced Gemini API interaction with better error handling."""
    if not text or len(text.strip()) < 50:  # Minimum text length check
        print("Insufficient text for processing")
        return None
    model = genai.GenerativeModel('gemini-2.0-flash')
    # Improved prompt with clearer instructions
    prompt = f"""
    You are a legal document analyzer. From the text provided, extract the following information. Do not summarize or change the wording.
    Required JSON structure:
    {{
        "journal": "journal name or empty string",
        "parties": "case parties or empty string",
        "court": "court name or empty string",
        "date_of_judgement": "YYYY-MM-DD format or empty string",
        "sections": "relevant sections or empty string",
        "description": "The full, verbatim text of the headnotes. Do not summarize.",
        "keywords": "A comma-separated list of 5-7 relevant legal keywords from the text."
    }}
    Important: Return ONLY the JSON object, no other text, no markdown formatting, no explanations.
    TEXT TO ANALYZE:
    {text[:8000]}
    """  # Limit text length to avoid token limits
    try:
        response = model.generate_content(prompt)
        raw_response = response.text.strip()
        print(f"Raw Gemini response: {raw_response[:200]}...")  # Show first 200 chars
        # Clean the response more thoroughly
        json_text = raw_response
        if json_text.startswith('```'):
            json_text = json_text[7:]
        if json_text.endswith('```'):
            json_text = json_text[:-3]
        json_text = json_text.strip()
        # Parse JSON
        parsed_data = json.loads(json_text)
        # Validate required fields exist
        required_fields = ["journal", "parties", "court", "date_of_judgement", "sections", "description", "keywords"]
        for field in required_fields:
            if field not in parsed_data:
                parsed_data[field] = ""  # Ensure all keys exist
        return parsed_data
    except json.JSONDecodeError as e:
        print(f"JSON parsing error: {e}")
        print(f"Failed to parse: {json_text[:500]}")
        return None
    except Exception as e:
        print(f"Gemini API error: {e}")
        return None

def build_fts_query(raw_query: str) -> str:
    """
    Builds a query string suitable for SQLite FTS5.
    - Wraps terms in double quotes.
    - Adds a prefix search operator (*) to terms longer than 2 chars.
    - Joins terms with OR for broader matching.
    """
    terms = raw_query.strip().split()
    processed = []
    for t in terms:
        if len(t) > 2:
            processed.append(f'"{t}"*')  # Allows partial match & phrase anchoring
        else:
            processed.append(f'"{t}"')
    return " OR ".join(processed)

def search_citations(raw_query):
    """
    Performs a ranked full-text search on the.
    Converts raw SQL results into Citation objects to ensure correct data types.
    """
    fts_query = build_fts_query(raw_query)
    sql = text("""
    SELECT c.*, bm25(citation_fts, 5.0, 3.0, 2.0, 1.5, 1.5, 1.0) AS score
    FROM citation_fts
    JOIN citation c ON c.id = citation_fts.rowid
    WHERE citation_fts MATCH :q
    ORDER BY score
    LIMIT 100;
    """)
    raw_results = db.session.execute(sql, {"q": fts_query}).fetchall()
    results = []
    for row in raw_results:
        # The Row object from SQLAlchemy can be treated like a dict.
        citation_data = dict(row._mapping)
        score = citation_data.pop('score')
        # Manually convert date string to a date object if it's a string
        date_val = citation_data.get('date_of_judgement')
        if isinstance(date_val, str):
            try:
                citation_data['date_of_judgement'] = datetime.strptime(date_val, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                # Handle cases where the date might be invalid or None
                citation_data['date_of_judgement'] = None
        # Create a Citation object from the data
        citation_obj = Citation(**citation_data)
        # Attach the relevance score to the object
        citation_obj.score = score
        results.append(citation_obj)
    return results

# --- Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/search')
def search():
    query = request.args.get('q', '').strip()
    if not query:
        return redirect(url_for('index'))
    results = search_citations(query)
    return render_template('search_results.html', citations=results, query=query)

@app.route('/add', methods=['GET', 'POST'])
def add_citation():
    if request.method == 'POST':
        try:
            new_citation = Citation(
                journal=request.form['journal'],
                parties=request.form['parties'],
                court=request.form['court'],
                date_of_judgement=datetime.strptime(request.form['date_of_judgement'], '%Y-%m-%d').date(),
                sections=request.form['sections'],
                description=request.form['description'],
                keywords=request.form.get('keywords'),
                pdf_path=request.form.get('pdf_path')
            )
            db.session.add(new_citation)
            db.session.commit()
            flash('Citation added successfully!', 'success')
        except Exception as e:
            flash(f'Error adding citation: {e}', 'error')
        return redirect(url_for('index'))
    extracted_data = session.pop('extracted_data', None)
    pdf_filename = session.pop('pdf_filename', None)
    return render_template('add_citation.html', data=extracted_data, pdf_filename=pdf_filename)

@app.route('/extract', methods=['POST'])
def extract_and_fill():
    # NOTE: On Render, uploads/ is ephemeral and resets on redeploy. For persistent storage, integrate cloud storage (e.g., AWS S3).
    if 'pdf_for_extraction' not in request.files:
        flash('No file part in the request.', 'error')
        return redirect(url_for('add_citation'))
    pdf_file = request.files['pdf_for_extraction']
    if pdf_file.filename == '':
        flash('No file selected for upload.', 'error')
        return redirect(url_for('add_citation'))
    if pdf_file:
        filename = secure_filename(pdf_file.filename)
        pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        pdf_file.save(pdf_path)
        print(f"PDF saved to: {pdf_path}")  # Debug log
        text = extract_text_from_pdf(pdf_path)
        print(f"Extracted text length: {len(text) if text else 0}")  # Debug log
        if text:
            extracted_data = get_info_from_gemini(text)
            print(f"Gemini returned: {extracted_data}")  # Debug log
            if extracted_data:
                session['extracted_data'] = extracted_data
                session['pdf_filename'] = filename
                flash('Information extracted successfully! Please review and save.', 'success')
            else:
                flash('AI extraction failed. Please check the logs and try again.', 'error')
        else:
            flash('Could not extract text from PDF. Please check the file format.', 'error')
    return redirect(url_for('add_citation'))

@app.route('/view/<int:citation_id>')
def view_citation(citation_id):
    citation = Citation.query.get_or_404(citation_id)
    return render_template('view_citation.html', citation=citation)

@app.route('/delete/<int:citation_id>', methods=['POST'])
def delete_citation(citation_id):
    citation = Citation.query.get_or_404(citation_id)
    try:
        db.session.delete(citation)
        db.session.commit()
        flash('Citation deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting citation: {e}', 'error')
    return redirect(url_for('index'))

@app.route('/uploads/<filename>')
def download_pdf(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = not os.environ.get('RENDER')  # Disable debug in production
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
