from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS
import pandas as pd
from io import BytesIO
from fpdf import FPDF
import chardet
import os
import traceback
import tempfile

app = Flask(__name__)
CORS(app)

# Global variable to hold uploaded dataset
dataset = pd.DataFrame()

@app.route('/')
def index():
    try:
        return render_template('index.html')
    except Exception as e:
        return f"""
        <h1>Error: index.html not found</h1>
        <p>Please create a 'templates' folder and place index.html inside it.</p>
        <p>Error: {str(e)}</p>
        """, 500

@app.route('/upload', methods=['POST'])
def upload_file():
    global dataset
    
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        # Read file content to detect encoding
        file_content = file.read()
        
        if len(file_content) == 0:
            return jsonify({'error': 'File is empty'}), 400
        
        # Detect encoding using chardet
        detected = chardet.detect(file_content)
        encoding = detected['encoding'] if detected['encoding'] else 'utf-8'
        
        print(f"Detected encoding: {encoding}")
        
        # Try to read CSV with detected encoding
        success = False
        for enc in [encoding, 'utf-8', 'latin-1', 'iso-8859-1', 'cp1252', 'windows-1252', 'utf-16']:
            try:
                dataset = pd.read_csv(BytesIO(file_content), encoding=enc)
                encoding = enc
                print(f"Successfully read CSV with encoding: {enc}")
                success = True
                break
            except Exception as e:
                print(f"Failed with {enc}: {str(e)}")
                continue
        
        if not success:
            return jsonify({'error': 'Unable to read CSV file. Please ensure it is a valid CSV format with proper encoding.'}), 400
        
        # Check if dataset is empty
        if dataset.empty:
            return jsonify({'error': 'CSV file is empty or has no data rows'}), 400
        
        # Strip whitespace from column names
        dataset.columns = dataset.columns.str.strip()
        
        print(f"CSV Columns found: {dataset.columns.tolist()}")
        
        # Try to find name column (case-insensitive)
        name_column = None
        possible_name_cols = ['name', 'student name', 'student', 'studentname', 'student_name']
        
        for col in dataset.columns:
            if col.lower().strip() in possible_name_cols:
                name_column = col
                print(f"Found name column: {col}")
                break
        
        if name_column is None:
            available_cols = ', '.join(dataset.columns.tolist())
            return jsonify({
                'error': f'CSV must contain a "Name" column. Found columns: {available_cols}. Please add a column named "Name" with student names.'
            }), 400
        
        # Rename to standard 'Name' if different
        if name_column != 'Name':
            dataset.rename(columns={name_column: 'Name'}, inplace=True)
        
        # Remove rows where Name is empty
        dataset['Name'] = dataset['Name'].astype(str)
        dataset = dataset[dataset['Name'].notna()]
        dataset = dataset[dataset['Name'].str.strip() != '']
        dataset = dataset[dataset['Name'].str.lower() != 'nan']
        
        if dataset.empty:
            return jsonify({'error': 'No valid student names found in the CSV. Name column appears to be empty.'}), 400
        
        # Identify subject columns (exclude Name and common ID columns)
        exclude_cols = ['Name', 'RegNo', 'Register Number', 'Registration Number', 'ID', 'Roll No', 'RollNo', 'Student ID', 'Roll_No', 'Reg_No']
        subjects = [col for col in dataset.columns if col not in exclude_cols]
        
        if not subjects:
            return jsonify({'error': 'No subject columns found in CSV. Please ensure you have columns with marks (e.g., Math, Science, English).'}), 400
        
        print(f"Successfully loaded {len(dataset)} students with subjects: {subjects}")
        
        return jsonify({
            'message': 'File uploaded successfully',
            'columns': dataset.columns.tolist(),
            'students': len(dataset),
            'subjects': subjects,
            'encoding': encoding
        }), 200
    
    except Exception as e:
        print(f"Upload error: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': f'Server error while processing CSV: {str(e)}'}), 500

@app.route('/progress_card', methods=['POST'])
def generate_progress_card():
    global dataset
    
    try:
        if dataset.empty:
            return jsonify({'error': 'No dataset uploaded. Please upload a CSV file first.'}), 400
        
        data = request.get_json()
        if not data or 'name' not in data:
            return jsonify({'error': 'Student name is required in request body'}), 400
        
        name = data.get('name', '').strip()
        
        if not name:
            return jsonify({'error': 'Student name cannot be empty'}), 400
        
        print(f"Searching for student: {name}")
        
        # Search for student (case-insensitive, strip whitespace)
        dataset['Name'] = dataset['Name'].astype(str).str.strip()
        student_match = dataset[dataset['Name'].str.lower() == name.lower()]
        
        if student_match.empty:
            # Try partial match
            student_match = dataset[dataset['Name'].str.lower().str.contains(name.lower(), na=False)]
            
            if student_match.empty:
                available_names = dataset['Name'].head(10).tolist()
                return jsonify({
                    'error': f'Student "{name}" not found. Available names: {", ".join(available_names)}'
                }), 404
            elif len(student_match) > 1:
                matched_names = student_match['Name'].tolist()
                return jsonify({
                    'error': f'Multiple students match "{name}": {", ".join(matched_names)}. Please use exact name.'
                }), 400
        
        student = student_match.iloc[0]
        print(f"Found student: {student['Name']}")
        
        # Identify subject columns (exclude Name and common ID columns)
        exclude_cols = ['Name', 'RegNo', 'Register Number', 'Registration Number', 'ID', 'Roll No', 'RollNo', 'Student ID', 'Roll_No', 'Reg_No']
        subjects = [col for col in dataset.columns if col not in exclude_cols]
        
        # Get marks for subjects (only numeric columns)
        marks_dict = {}
        for subject in subjects:
            try:
                mark = pd.to_numeric(student[subject], errors='coerce')
                if pd.notna(mark):
                    marks_dict[subject] = float(mark)
            except Exception as e:
                print(f"Could not convert {subject} to numeric: {e}")
                continue
        
        if not marks_dict:
            return jsonify({'error': f'No valid numeric marks found for student "{name}". Please check the CSV data.'}), 400
        
        print(f"Marks found: {marks_dict}")
        
        # Calculate statistics
        marks_series = pd.Series(marks_dict)
        total = marks_series.sum()
        average = marks_series.mean()
        max_marks = len(marks_dict) * 100  # Assuming each subject is out of 100
        percentage = (total / max_marks) * 100 if max_marks > 0 else 0
        
        # Determine grade
        if average >= 90:
            grade = 'A+ (Outstanding)'
        elif average >= 80:
            grade = 'A (Excellent)'
        elif average >= 70:
            grade = 'B (Very Good)'
        elif average >= 60:
            grade = 'C (Good)'
        elif average >= 50:
            grade = 'D (Satisfactory)'
        else:
            grade = 'F (Needs Improvement)'
        
        top_subject = marks_series.idxmax()
        weak_subject = marks_series.idxmin()
        
        # Generate PDF
        pdf = FPDF()
        pdf.add_page()
        
        # Header with border
        pdf.set_fill_color(70, 130, 180)
        pdf.rect(10, 10, 190, 25, 'F')
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Arial", 'B', 20)
        pdf.set_y(20)
        pdf.cell(0, 10, "STUDENT PROGRESS CARD", ln=True, align='C')
        
        pdf.set_text_color(0, 0, 0)
        pdf.ln(15)
        
        # Student Information Section
        pdf.set_fill_color(240, 240, 240)
        pdf.rect(10, pdf.get_y(), 190, 8, 'F')
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(0, 8, "STUDENT INFORMATION", ln=True)
        pdf.ln(2)
        
        pdf.set_font("Arial", '', 12)
        pdf.cell(50, 8, "Name:", 0)
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 8, str(student['Name']), ln=True)
        
        # Add RegNo if exists
        reg_cols = ['RegNo', 'Register Number', 'Registration Number', 'ID', 'Roll No', 'RollNo']
        for reg_col in reg_cols:
            if reg_col in dataset.columns and pd.notna(student[reg_col]):
                pdf.set_font("Arial", '', 12)
                pdf.cell(50, 8, f"{reg_col}:", 0)
                pdf.set_font("Arial", 'B', 12)
                pdf.cell(0, 8, str(student[reg_col]), ln=True)
                break
        
        pdf.ln(5)
        
        # Subject Marks Section
        pdf.set_fill_color(240, 240, 240)
        pdf.rect(10, pdf.get_y(), 190, 8, 'F')
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(0, 8, "SUBJECT-WISE MARKS", ln=True)
        pdf.ln(2)
        
        # Table header
        pdf.set_fill_color(200, 220, 255)
        pdf.set_font("Arial", 'B', 11)
        pdf.cell(130, 8, "Subject", 1, 0, 'L', True)
        pdf.cell(60, 8, "Marks Obtained", 1, 1, 'C', True)
        
        # Table rows
        pdf.set_font("Arial", '', 11)
        for subject, mark in marks_dict.items():
            pdf.cell(130, 8, subject, 1)
            pdf.cell(60, 8, f"{mark:.2f}", 1, 1, 'C')
        
        pdf.ln(5)
        
        # Performance Summary Section
        pdf.set_fill_color(240, 240, 240)
        pdf.rect(10, pdf.get_y(), 190, 8, 'F')
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(0, 8, "PERFORMANCE SUMMARY", ln=True)
        pdf.ln(2)
        
        pdf.set_font("Arial", '', 12)
        pdf.cell(60, 8, "Total Marks:", 0)
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 8, f"{total:.2f} / {max_marks}", ln=True)
        
        pdf.set_font("Arial", '', 12)
        pdf.cell(60, 8, "Percentage:", 0)
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 8, f"{percentage:.2f}%", ln=True)
        
        pdf.set_font("Arial", '', 12)
        pdf.cell(60, 8, "Average Marks:", 0)
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 8, f"{average:.2f}", ln=True)
        
        pdf.set_font("Arial", '', 12)
        pdf.cell(60, 8, "Grade:", 0)
        pdf.set_font("Arial", 'B', 12)
        pdf.set_text_color(0, 128, 0)
        pdf.cell(0, 8, grade, ln=True)
        pdf.set_text_color(0, 0, 0)
        
        pdf.ln(3)
        pdf.set_font("Arial", '', 12)
        pdf.cell(60, 8, "Best Performance:", 0)
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 8, f"{top_subject} ({marks_dict[top_subject]:.2f})", ln=True)
        
        pdf.set_font("Arial", '', 12)
        pdf.cell(60, 8, "Needs Improvement:", 0)
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 8, f"{weak_subject} ({marks_dict[weak_subject]:.2f})", ln=True)
        
        # Footer
        pdf.ln(10)
        pdf.set_font("Arial", 'I', 9)
        pdf.set_text_color(128, 128, 128)
        pdf.cell(0, 8, f"Generated on: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}", ln=True, align='C')
        
        # Save PDF to temporary file (works with all FPDF versions)
        temp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        pdf.output(temp_pdf.name)
        temp_pdf.close()
        
        print(f"PDF generated successfully for {student['Name']}")
        
        # Read the file into BytesIO
        with open(temp_pdf.name, 'rb') as f:
            pdf_buffer = BytesIO(f.read())
        
        # Clean up temp file
        os.unlink(temp_pdf.name)
        
        pdf_buffer.seek(0)
        
        return send_file(
            pdf_buffer, 
            download_name=f"{student['Name']}_progress_card.pdf", 
            as_attachment=True,
            mimetype='application/pdf'
        )
    
    except Exception as e:
        print(f"Progress card generation error: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': f'Server error while generating PDF: {str(e)}'}), 500

# Error handlers
@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    # Create templates folder if it doesn't exist
    if not os.path.exists('templates'):
        os.makedirs('templates')
        print("✅ Created 'templates' folder. Please place index.html inside it.")
    
    print("=" * 50)
    print("🚀 Starting Flask Server...")
    print("=" * 50)
    print("📂 Make sure index.html is in the 'templates' folder")
    print("🌐 Open browser: http://localhost:5000")
    print("=" * 50)
    
    app.run(debug=True, host='0.0.0.0', port=5000)
