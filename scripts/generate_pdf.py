from fpdf import FPDF

def generate_pdf():
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    
    with open('KT_DOCUMENT.md', 'r') as f:
        content = f.read()
    
    # Simple replacement to avoid encoding / character issues
    content = content.replace('•', '-').replace('\u2013', '-').replace('\u2014', '-')
    content = content.replace('✅', '[DO]').replace('❌', '[DONT]')
    
    # Strip any remaining emojis/unicode unsupported by core fonts
    content = content.encode('ascii', errors='ignore').decode('ascii')
    
    for line in content.split('\n'):
        line = line.strip()
        if not line:
            pdf.ln(5)
            continue
            
        if line.startswith('#'):
            level = line.count('#')
            title = line.replace('#', '').strip()
            if level == 1:
                pdf.set_font("Helvetica", 'B', size=16)
                pdf.cell(190, 10, txt=title.upper(), ln=1, align='L')
            else:
                pdf.set_font("Helvetica", 'B', size=14)
                pdf.cell(190, 10, txt=title, ln=1, align='L')
            pdf.set_font("Helvetica", size=12)
        elif line.startswith('---'):
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(2)
        else:
            pdf.multi_cell(190, 8, txt=line)
            
    pdf.output("KT_DOCUMENT.pdf")
    print("PDF generation finished.")

if __name__ == "__main__":
    try:
        generate_pdf()
    except Exception as e:
        print(f"Error: {e}")
