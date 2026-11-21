import io
import json
import os
import re
import time
from xml.sax.saxutils import escape

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor
from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import Question


# ---------------------------------------------------------------------------
# Math Symbol <-> LaTeX Helpers
# ---------------------------------------------------------------------------

UNICODE_TO_LATEX = {
    '∫': r'\int',
    '∬': r'\iint',
    '∑': r'\sum',
    '∏': r'\prod',
    '∂': r'\partial',
    '∇': r'\nabla',
    '√': r'\sqrt',
    '≤': r'\le',
    '≥': r'\ge',
    '≠': r'\ne',
    '≈': r'\approx',
    '∞': r'\infty',
    '±': r'\pm',
    '−': '-',
    '×': r'\times',
    '÷': r'\div',
    '·': r'\cdot',
    'π': r'\pi',
    'θ': r'\theta',
    'α': r'\alpha',
    'β': r'\beta',
    'γ': r'\gamma',
    'δ': r'\delta',
    'Δ': r'\Delta',
    'λ': r'\lambda',
    'μ': r'\mu',
    'σ': r'\sigma',
    'ω': r'\omega',
    'Σ': r'\Sigma',
    'Π': r'\Pi',
    'Ω': r'\Omega',
    '→': r'\to',
    '⇒': r'\Rightarrow',
    '⇔': r'\Leftrightarrow',
    '∈': r'\in',
    '∉': r'\notin',
    '⊂': r'\subset',
    '⊆': r'\subseteq',
    '∀': r'\forall',
    '∃': r'\exists',
    '∪': r'\cup',
    '∩': r'\cap',
    '°': r'^\circ',
    '⁰': r'^0',
    '¹': r'^1',
    '²': r'^2',
    '³': r'^3',
    '⁴': r'^4',
    '⁵': r'^5',
    '⁶': r'^6',
    '⁷': r'^7',
    '⁸': r'^8',
    '⁹': r'^9',
    'ⁿ': r'^n',
    '₀': r'_0',
    '₁': r'_1',
    '₂': r'_2',
    '₃': r'_3',
    '½': r'\frac{1}{2}',
    '⅓': r'\frac{1}{3}',
    '¼': r'\frac{1}{4}',
    '¾': r'\frac{3}{4}',
}

LATEX_TO_UNICODE = {
    r'\int': '∫',
    r'\iint': '∬',
    r'\sum': '∑',
    r'\prod': '∏',
    r'\sqrt': '√',
    r'\le': '≤',
    r'\leq': '≤',
    r'\ge': '≥',
    r'\geq': '≥',
    r'\ne': '≠',
    r'\neq': '≠',
    r'\approx': '≈',
    r'\infty': '∞',
    r'\pm': '±',
    r'\times': '×',
    r'\div': '÷',
    r'\cdot': '·',
    r'\pi': 'π',
    r'\theta': 'θ',
    r'\alpha': 'α',
    r'\beta': 'β',
    r'\gamma': 'γ',
    r'\delta': 'δ',
    r'\Delta': 'Δ',
    r'\lambda': 'λ',
    r'\mu': 'μ',
    r'\sigma': 'σ',
    r'\Omega': 'Ω',
    r'\to': '→',
    r'\rightarrow': '→',
    r'\Rightarrow': '⇒',
    r'\in': '∈',
    r'\notin': '∉',
    r'\forall': '∀',
    r'\exists': '∃',
    r'\cup': '∪',
    r'\cap': '∩',
    r'\circ': '°',
    r'\,': ' ',
    r'\;': ' ',
    r'\quad': '  ',
}


def unicode_math_to_latex(text):
    """Wrap and normalize Unicode math symbols in text into clean MathJax LaTeX."""
    if not text:
        return ''
    has_math = any(ch in text for ch in UNICODE_TO_LATEX)
    if not has_math:
        return text.strip()

    # If text already has explicit LaTeX delimiters, preserve them and only convert stray Unicode
    if r'\(' in text or '$$' in text:
        out = text
        for uni, tex in UNICODE_TO_LATEX.items():
            out = out.replace(uni, f' {tex} ')
        return re.sub(r'\s{2,}', ' ', out).strip()

    # Convert common symbol tokens inline with \( ... \)
    out = text
    for uni, tex in UNICODE_TO_LATEX.items():
        if uni in out:
            if tex.startswith('^'):
                out = re.sub(
                    rf'([A-Za-z0-9\)]+){re.escape(uni)}',
                    lambda m, t=tex: f'\\({m.group(1)}{t}\\)',
                    out,
                )
                out = out.replace(uni, f'\\({tex}\\)')
            else:
                out = out.replace(uni, f'\\({tex}\\)')
    # Merge adjacent \( ... \) \( ... \) into single math expression
    out = re.sub(r'\\\)\s*\\\(', ' ', out)
    return re.sub(r'\s{2,}', ' ', out).strip()


def latex_to_readable_math(text):
    """Convert MathJax LaTeX expressions into clean Unicode readable math for Word/PDF output."""
    if not text:
        return ''
    out = text
    out = out.replace('$$', '').replace(r'\(', '').replace(r'\)', '').replace(r'\[', '').replace(r'\]', '')
    # Convert \frac{a}{b} -> (a)/(b)
    out = re.sub(r'\\frac\{([^{}]+)\}\{([^{}]+)\}', r'(\1)/(\2)', out)
    # Convert \sqrt{x} -> √(x)
    out = re.sub(r'\\sqrt\{([^{}]+)\}', r'√(\1)', out)
    for tex, uni in sorted(LATEX_TO_UNICODE.items(), key=lambda x: -len(x[0])):
        out = out.replace(tex, uni)
    out = out.replace('^2', '²').replace('^3', '³').replace('^1', '¹').replace('^0', '⁰')
    out = re.sub(r'_\{([^{}]+)\}', r'_\1', out)
    out = re.sub(r'\^\{([^{}]+)\}', r'^\1', out)
    out = out.replace('{', '').replace('}', '')
    return re.sub(r'\s{2,}', ' ', out).strip()


# ---------------------------------------------------------------------------
# PDF Question Recognition (Gemini AI + Local pypdf Math Parser)
# ---------------------------------------------------------------------------

def _normalize_recognized_question(item):
    qtype = str(item.get('question_type', 'SHORT')).strip().upper()
    if qtype not in (Question.TYPE_MCQ, Question.TYPE_FITB, Question.TYPE_SHORT):
        if item.get('option_a') and item.get('option_b'):
            qtype = Question.TYPE_MCQ
        elif '____' in str(item.get('prompt', '')):
            qtype = Question.TYPE_FITB
        else:
            qtype = Question.TYPE_SHORT

    try:
        marks = max(1, int(item.get('marks', 1)))
    except (TypeError, ValueError):
        marks = 1

    diff = str(item.get('difficulty', 'MEDIUM')).strip().upper()
    if diff not in (Question.DIFF_EASY, Question.DIFF_MEDIUM, Question.DIFF_HARD):
        diff = Question.DIFF_MEDIUM

    correct_opt = str(item.get('correct_option', '')).strip().upper()[:1]
    if correct_opt not in ('A', 'B', 'C', 'D'):
        correct_opt = ''

    return {
        'question_type': qtype,
        'prompt': str(item.get('prompt', '')).strip(),
        'option_a': str(item.get('option_a', '')).strip(),
        'option_b': str(item.get('option_b', '')).strip(),
        'option_c': str(item.get('option_c', '')).strip(),
        'option_d': str(item.get('option_d', '')).strip(),
        'correct_option': correct_opt,
        'correct_answer_text': str(item.get('correct_answer_text', '')).strip(),
        'model_answer': str(item.get('model_answer', '')).strip(),
        'marks': marks,
        'difficulty': diff,
    }


def _extract_questions_via_gemini(pdf_bytes):
    from google import genai
    from google.genai import types

    api_key = os.environ.get('GEMINI_API_KEY', '').strip()
    if not api_key:
        raise RuntimeError('GEMINI_API_KEY is not configured in environment secrets.')

    client = genai.Client(
        api_key=api_key,
        http_options={'headers': {'User-Agent': 'aistudio-build'}},
    )

    instruction = (
        "You are an expert academic exam digitizer. Extract ALL questions from the attached PDF. "
        "Preserve and accurately transcribe every mathematical symbol, equation, fraction, integral, summation, "
        "matrix, root, exponent, subscript, and Greek letter into MathJax LaTeX using \\( ... \\) for inline math "
        "or $$ ... $$ for display math. Classify each question as 'MCQ' (if options A/B/C/D are present), "
        "'FITB' (fill in the blanks), or 'SHORT' (short/descriptive answer). Extract marks if printed (default 1), "
        "and include any printed answer key or model solution. Return ONLY a JSON array of objects."
    )

    schema = types.Schema(
        type=types.Type.ARRAY,
        items=types.Schema(
            type=types.Type.OBJECT,
            properties={
                'question_type': types.Schema(type=types.Type.STRING),
                'prompt': types.Schema(type=types.Type.STRING),
                'option_a': types.Schema(type=types.Type.STRING),
                'option_b': types.Schema(type=types.Type.STRING),
                'option_c': types.Schema(type=types.Type.STRING),
                'option_d': types.Schema(type=types.Type.STRING),
                'correct_option': types.Schema(type=types.Type.STRING),
                'correct_answer_text': types.Schema(type=types.Type.STRING),
                'model_answer': types.Schema(type=types.Type.STRING),
                'marks': types.Schema(type=types.Type.INTEGER),
                'difficulty': types.Schema(type=types.Type.STRING),
            },
            required=['question_type', 'prompt', 'marks'],
        ),
    )

    last_exc = None
    response = None
    for attempt_idx in range(2):
        try:
            response = client.models.generate_content(
                model='gemini-4-hydrogen',
                contents=[
                    types.Part.from_bytes(data=pdf_bytes, mime_type='application/pdf'),
                    'Extract all questions and convert all mathematical formulas into MathJax LaTeX.',
                ],
                config=types.GenerateContentConfig(
                    system_instruction=instruction,
                    response_mime_type='application/json',
                    response_schema=schema,
                ),
            )
            break
        except Exception as exc:
            last_exc = exc
            err_str = str(exc)
            if ('429' in err_str or 'RESOURCE_EXHAUSTED' in err_str) and attempt_idx == 0:
                m_delay = re.search(r'retry in (\d+(?:\.\d+)?)s', err_str, re.IGNORECASE)
                delay_secs = min(4.5, float(m_delay.group(1)) + 0.4) if m_delay else 2.8
                time.sleep(delay_secs)
                continue
            if '429' in err_str or 'RESOURCE_EXHAUSTED' in err_str:
                raise RuntimeError(
                    'Gemini API free-tier quota limit (5 requests/min) exceeded (429 RESOURCE_EXHAUSTED). '
                    'Wait a few seconds and retry, upgrade your API key in Settings > Secrets, '
                    'or select "Local PDF Math Symbol Parser (pypdf, offline)" for text-based PDFs.'
                ) from exc
            raise

    if response is None and last_exc is not None:
        raise last_exc

    raw_text = (response.text or '[]').strip()
    parsed = json.loads(raw_text)
    if not isinstance(parsed, list):
        raise ValueError('Unexpected AI response format.')
    return [_normalize_recognized_question(item) for item in parsed if str(item.get('prompt', '')).strip()]


def _extract_questions_via_local_pdf(pdf_bytes):
    """Pure-Python pypdf parser with Unicode math-to-LaTeX conversion and MCQ/FITB/SHORT detection."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    full_text = '\n'.join((page.extract_text() or '') for page in reader.pages)
    if not full_text.strip():
        return []

    lines = [line.strip() for line in full_text.splitlines() if line.strip()]
    q_start_re = re.compile(r'^(?:Q(?:uestion)?\s*\.?\s*\d+[\.\):]|\(\d{1,3}\)|(?:\d{1,3})[\.\)])\s*(.+)$', re.IGNORECASE)
    opt_re = re.compile(r'^(?:\(?([A-Da-d])\)?[\.\):])\s*(.+)$')
    inline_opts_re = re.compile(r'\(([A-Da-d])\)\s*(.*?)(?=\s*\([A-Da-d]\)|$)')
    ans_re = re.compile(r'^(?:Ans(?:wer)?|Correct\s*Answer|Key|Sol(?:ution)?)\s*[:\-]\s*(.+)$', re.IGNORECASE)
    marks_re = re.compile(r'[\[\(]\s*(\d+)\s*(?:marks?|pts?|m)\s*[\]\)]', re.IGNORECASE)

    blocks = []
    current = None

    for line in lines:
        qm = q_start_re.match(line)
        if qm:
            if current:
                blocks.append(current)
            current = {'lines': [qm.group(1)], 'options': {}, 'answer': '', 'marks': 1}
            mm = marks_re.search(line)
            if mm:
                current['marks'] = int(mm.group(1))
                current['lines'][0] = marks_re.sub('', current['lines'][0]).strip()
            continue

        if current is None:
            continue

        # Check if multiple inline options exist on a single line like "(A) 2 (B) 4 (C) 6 (D) 8"
        inline_matches = inline_opts_re.findall(line)
        if len(inline_matches) >= 2:
            for letter, val in inline_matches:
                if val.strip():
                    current['options'][letter.upper()] = unicode_math_to_latex(val.strip())
            continue

        om = opt_re.match(line)
        if om:
            opt_letter = om.group(1).upper()
            current['options'][opt_letter] = unicode_math_to_latex(om.group(2))
            continue

        am = ans_re.match(line)
        if am:
            current['answer'] = am.group(1).strip()
            continue

        mm = marks_re.search(line)
        if mm:
            current['marks'] = int(mm.group(1))
            line = marks_re.sub('', line).strip()

        if line:
            current['lines'].append(line)

    if current:
        blocks.append(current)

    # If no numbered questions matched, split by blank-line paragraphs
    if not blocks:
        for para in re.split(r'\n\s*\n', full_text):
            cleaned = para.strip()
            if len(cleaned) >= 8:
                blocks.append({'lines': [cleaned], 'options': {}, 'answer': '', 'marks': 1})

    results = []
    for blk in blocks:
        prompt_raw = ' '.join(blk['lines']).strip()
        prompt_tex = unicode_math_to_latex(prompt_raw)
        opts = blk['options']
        ans = blk['answer']

        if len(opts) >= 2:
            qtype = Question.TYPE_MCQ
            corr_opt = ''
            if ans:
                m_opt = re.search(r'\b([A-D])\b', ans.upper())
                if m_opt:
                    corr_opt = m_opt.group(1)
            results.append(_normalize_recognized_question({
                'question_type': qtype,
                'prompt': prompt_tex,
                'option_a': opts.get('A', ''),
                'option_b': opts.get('B', ''),
                'option_c': opts.get('C', ''),
                'option_d': opts.get('D', ''),
                'correct_option': corr_opt or 'A',
                'marks': blk['marks'],
            }))
        elif '____' in prompt_tex or '___' in prompt_tex or 'fill in the blank' in prompt_tex.lower():
            results.append(_normalize_recognized_question({
                'question_type': Question.TYPE_FITB,
                'prompt': prompt_tex,
                'correct_answer_text': unicode_math_to_latex(ans) if ans else 'TBD',
                'marks': blk['marks'],
            }))
        else:
            results.append(_normalize_recognized_question({
                'question_type': Question.TYPE_SHORT,
                'prompt': prompt_tex,
                'model_answer': unicode_math_to_latex(ans),
                'marks': blk['marks'],
            }))

    return results


def extract_questions_from_pdf(uploaded_file, mode='auto'):
    """
    Extract questions + math symbols from an uploaded PDF.
    - mode == 'local': Offline pypdf + Unicode-to-LaTeX math parser.
    - mode == 'ai': Gemini AI (`gemini-4-hydrogen`) for scanned PDFs and complex equations.
    - mode == 'auto': Uses Local pypdf first for text-selectable PDFs (zero API quota usage),
      and automatically invokes Gemini AI when the PDF is scanned / yields no text questions.
    Returns (questions_list, engine_used_label).
    """
    pdf_bytes = uploaded_file.read()
    has_key = bool(os.environ.get('GEMINI_API_KEY', '').strip())

    if mode == 'local':
        questions = _extract_questions_via_local_pdf(pdf_bytes)
        return questions, 'Local PDF Math Symbol Parser (pypdf)'

    if mode == 'ai':
        questions = _extract_questions_via_gemini(pdf_bytes)
        return questions, 'Gemini AI Math & Scanned PDF Recognition (gemini-4-hydrogen)'

    # Auto mode: fast local extraction first for digital PDFs; Gemini AI for scanned/unparsed PDFs
    local_questions = _extract_questions_via_local_pdf(pdf_bytes)
    if local_questions:
        return local_questions, 'Local PDF Math Symbol Parser (pypdf)'

    if has_key:
        questions = _extract_questions_via_gemini(pdf_bytes)
        return questions, 'Gemini AI Math & Scanned PDF Recognition (gemini-4-hydrogen)'

    return [], 'Local PDF Math Symbol Parser (pypdf)'


# ---------------------------------------------------------------------------
# Paper Format Upload Parser & Word (.docx) + PDF (.pdf) Question Paper Builder
# ---------------------------------------------------------------------------

def parse_paper_format(uploaded_file):
    """Parse an uploaded .docx, .pdf, or .txt format blueprint into structured paper styling & sections."""
    filename = (uploaded_file.name or '').lower()
    raw_bytes = uploaded_file.read()
    lines = []

    if filename.endswith('.docx'):
        doc = docx.Document(io.BytesIO(raw_bytes))
        for p in doc.paragraphs:
            if p.text.strip():
                lines.append(p.text.strip())
        for tbl in doc.tables:
            for row in tbl.rows:
                row_txt = ' | '.join(c.text.strip() for c in row.cells if c.text.strip())
                if row_txt:
                    lines.append(row_txt)
    elif filename.endswith('.pdf'):
        reader = PdfReader(io.BytesIO(raw_bytes))
        for page in reader.pages:
            txt = page.extract_text() or ''
            for line in txt.splitlines():
                if line.strip():
                    lines.append(line.strip())
    else:
        txt = raw_bytes.decode('utf-8', errors='replace')
        lines = [line.strip() for line in txt.splitlines() if line.strip()]

    institution = 'ACADEX INSTITUTION OF MANAGEMENT & COMMERCE'
    subtitle = ''
    instructions = []
    sections = []
    footer_note = '*** END OF QUESTION PAPER ***'

    section_re = re.compile(r'^(?:section|part|unit)\s+[A-Z0-9IVX]+.*$', re.IGNORECASE)
    instr_mode = False

    for idx, line in enumerate(lines):
        if idx == 0 and not line.startswith('{{'):
            institution = line
            continue
        if idx == 1 and not section_re.match(line) and 'instruction' not in line.lower():
            subtitle = line
            continue
        if 'instruction' in line.lower():
            instr_mode = True
            continue
        if section_re.match(line):
            instr_mode = False
            low = line.lower()
            if 'mcq' in low or 'multiple choice' in low or 'objective' in low:
                qtype = 'MCQ'
            elif 'blank' in low or 'fitb' in low:
                qtype = 'FITB'
            elif 'short' in low or 'descriptive' in low or 'essay' in low:
                qtype = 'SHORT'
            else:
                qtype = 'ALL'
            sections.append({'title': line, 'qtype': qtype})
            continue
        if 'end of' in line.lower() or '***' in line:
            footer_note = line
            continue
        if instr_mode and len(instructions) < 6:
            instructions.append(line.lstrip('•-*0123456789.) '))

    if not sections:
        sections = [
            {'title': 'SECTION A — MULTIPLE CHOICE QUESTIONS', 'qtype': 'MCQ'},
            {'title': 'SECTION B — FILL IN THE BLANKS', 'qtype': 'FITB'},
            {'title': 'SECTION C — SHORT ANSWER QUESTIONS', 'qtype': 'SHORT'},
        ]

    return {
        'source_filename': uploaded_file.name,
        'institution': institution,
        'subtitle': subtitle,
        'instructions': instructions,
        'sections': sections,
        'footer_note': footer_note,
        'is_docx_template': filename.endswith('.docx'),
        'has_placeholders': any('{{' in l and '}}' in l for l in lines),
    }


def build_sample_format_docx():
    """Create a downloadable sample .docx Question Paper Format template."""
    doc = docx.Document()
    h = doc.add_paragraph()
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = h.add_run('ACADEX UNIVERSITY — END-SEMESTER EXAMINATION')
    r.bold = True
    r.font.size = Pt(15)

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub.add_run('{{TITLE}} | Program: {{CLASS}} | Subject: {{SUBJECT}} | Time: {{DURATION}} | Max Marks: {{MARKS}}')

    doc.add_paragraph('General Instructions:')
    doc.add_paragraph('1. All questions are compulsory unless stated otherwise.')
    doc.add_paragraph('2. Write legible steps for mathematical problems.')

    doc.add_paragraph('SECTION A — MULTIPLE CHOICE QUESTIONS (MCQ)')
    doc.add_paragraph('SECTION B — FILL IN THE BLANKS (FITB)')
    doc.add_paragraph('SECTION C — SHORT ANSWER QUESTIONS (SHORT)')
    doc.add_paragraph('*** END OF QUESTION PAPER ***')

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.getvalue()


def _group_exam_questions_by_format(exam, fmt_config):
    """Group exam questions into the sections defined by the uploaded format."""
    all_questions = [eq.question for eq in exam.exam_questions.select_related('question').all()]
    sections = fmt_config.get('sections') or [{'title': 'QUESTIONS', 'qtype': 'ALL'}]
    grouped = []
    used_ids = set()

    for sec in sections:
        qtype = sec.get('qtype', 'ALL')
        if qtype == 'ALL':
            matched = [q for q in all_questions if q.id not in used_ids]
        else:
            matched = [q for q in all_questions if q.question_type == qtype and q.id not in used_ids]
        if matched:
            used_ids.update(q.id for q in matched)
            grouped.append({'title': sec['title'], 'questions': matched})

    remaining = [q for q in all_questions if q.id not in used_ids]
    if remaining:
        grouped.append({'title': 'ADDITIONAL QUESTIONS', 'questions': remaining})

    return grouped


def generate_word_paper(exam, fmt_config, include_answer_key=False, template_docx_bytes=None):
    """Generate a formatted Word (.docx) question paper matching the uploaded paper format."""
    grouped = _group_exam_questions_by_format(exam, fmt_config)

    # If user uploaded a .docx with {{...}} placeholders, preserve its template paragraphs/tables
    if template_docx_bytes and fmt_config.get('has_placeholders'):
        doc = docx.Document(io.BytesIO(template_docx_bytes))
        replacements = {
            '{{TITLE}}': exam.title,
            '{{CLASS}}': exam.academic_class.name,
            '{{SUBJECT}}': f"{exam.subject.code} - {exam.subject.name}",
            '{{DURATION}}': f"{exam.duration_minutes} Minutes",
            '{{MARKS}}': str(exam.total_marks),
        }
        for p in doc.paragraphs:
            for k, v in replacements.items():
                if k in p.text:
                    for run in p.runs:
                        if k in run.text:
                            run.text = run.text.replace(k, v)
                    if k in p.text:
                        p.text = p.text.replace(k, v)
    else:
        doc = docx.Document()
        for sec in doc.sections:
            sec.top_margin = Inches(0.75)
            sec.bottom_margin = Inches(0.75)
            sec.left_margin = Inches(0.8)
            sec.right_margin = Inches(0.8)

        # Institution Header
        h_p = doc.add_paragraph()
        h_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        h_run = h_p.add_run(fmt_config.get('institution', 'ACADEX LMS'))
        h_run.bold = True
        h_run.font.size = Pt(15)
        h_run.font.color.rgb = RGBColor(15, 41, 74)

        if fmt_config.get('subtitle'):
            sub_p = doc.add_paragraph()
            sub_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            sub_r = sub_p.add_run(fmt_config['subtitle'])
            sub_r.font.size = Pt(11)

        title_p = doc.add_paragraph()
        title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        t_run = title_p.add_run(exam.title)
        t_run.bold = True
        t_run.font.size = Pt(13)

        # Metadata Table
        meta_tbl = doc.add_table(rows=2, cols=2)
        meta_tbl.style = 'Table Grid'
        meta_tbl.cell(0, 0).text = f"Class / Program: {exam.academic_class.name}"
        meta_tbl.cell(0, 1).text = f"Subject: {exam.subject.code} - {exam.subject.name}"
        meta_tbl.cell(1, 0).text = f"Duration: {exam.duration_minutes} Minutes"
        meta_tbl.cell(1, 1).text = f"Maximum Marks: {exam.total_marks}"
        doc.add_paragraph()

        # Instructions
        instr_list = fmt_config.get('instructions') or ([exam.instructions] if exam.instructions else [])
        if instr_list:
            ip = doc.add_paragraph()
            ir = ip.add_run('General Instructions:')
            ir.bold = True
            for line in instr_list:
                doc.add_paragraph(f"• {line}")

    # Append Sections & Questions cleanly
    q_num = 1
    for sec in grouped:
        sp = doc.add_paragraph()
        sp.paragraph_format.space_before = Pt(10)
        sp.paragraph_format.space_after = Pt(4)
        sr = sp.add_run(sec['title'])
        sr.bold = True
        sr.font.size = Pt(11.5)
        sr.font.color.rgb = RGBColor(29, 78, 216)

        for q in sec['questions']:
            qp = doc.add_paragraph()
            qp.paragraph_format.space_before = Pt(4)
            qp.paragraph_format.space_after = Pt(2)
            qr_num = qp.add_run(f"Q{q_num}. ")
            qr_num.bold = True
            qp.add_run(f"{latex_to_readable_math(q.prompt)}   [{q.marks} Marks]")

            if q.question_type == Question.TYPE_MCQ:
                opts = [
                    ('A', q.option_a),
                    ('B', q.option_b),
                    ('C', q.option_c),
                    ('D', q.option_d),
                ]
                opt_text = '    '.join(f"({k}) {latex_to_readable_math(v)}" for k, v in opts if v)
                op = doc.add_paragraph(f"    {opt_text}")
                op.paragraph_format.space_after = Pt(4)
            elif q.question_type == Question.TYPE_FITB:
                doc.add_paragraph("    Answer: ____________________________________________")
            else:
                doc.add_paragraph("    ________________________________________________________________")
                doc.add_paragraph("    ________________________________________________________________")
            q_num += 1

    fp = doc.add_paragraph()
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fp.paragraph_format.space_before = Pt(12)
    fr = fp.add_run(fmt_config.get('footer_note', '*** END OF QUESTION PAPER ***'))
    fr.bold = True

    if include_answer_key:
        doc.add_page_break()
        ak_head = doc.add_paragraph()
        ak_head.alignment = WD_ALIGN_PARAGRAPH.CENTER
        ak_r = ak_head.add_run(f"ANSWER KEY — {exam.title}")
        ak_r.bold = True
        ak_r.font.size = Pt(13)

        ans_num = 1
        for sec in grouped:
            for q in sec['questions']:
                ap = doc.add_paragraph()
                ar = ap.add_run(f"Q{ans_num}: ")
                ar.bold = True
                ap.add_run(latex_to_readable_math(q.get_correct_display()))
                ans_num += 1

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.getvalue()


def _register_pdf_fonts():
    regular = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    bold = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
    if os.path.exists(regular) and os.path.exists(bold):
        if 'AcadexSans' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('AcadexSans', regular))
            pdfmetrics.registerFont(TTFont('AcadexSans-Bold', bold))
        return 'AcadexSans', 'AcadexSans-Bold'
    return 'Helvetica', 'Helvetica-Bold'


def generate_pdf_paper(exam, fmt_config, include_answer_key=False):
    """Generate a formatted PDF (.pdf) question paper matching the uploaded paper format."""
    font_reg, font_bold = _register_pdf_fonts()
    grouped = _group_exam_questions_by_format(exam, fmt_config)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=exam.title,
    )

    styles = getSampleStyleSheet()
    st_inst = ParagraphStyle('Inst', parent=styles['Normal'], fontName=font_bold, fontSize=14, alignment=1, textColor=colors.HexColor('#0f294a'), spaceAfter=4)
    st_sub = ParagraphStyle('Sub', parent=styles['Normal'], fontName=font_reg, fontSize=10, alignment=1, spaceAfter=4)
    st_title = ParagraphStyle('Title', parent=styles['Normal'], fontName=font_bold, fontSize=12, alignment=1, spaceAfter=8)
    st_sec = ParagraphStyle('Sec', parent=styles['Normal'], fontName=font_bold, fontSize=11, textColor=colors.HexColor('#1d4ed8'), spaceBefore=10, spaceAfter=6)
    st_q = ParagraphStyle('Q', parent=styles['Normal'], fontName=font_reg, fontSize=10, leading=14, spaceBefore=4, spaceAfter=3)
    st_opt = ParagraphStyle('Opt', parent=styles['Normal'], fontName=font_reg, fontSize=9.5, leading=13, leftIndent=14, spaceAfter=5)
    st_meta = ParagraphStyle('Meta', parent=styles['Normal'], fontName=font_reg, fontSize=9.5, leading=12)

    story = [
        Paragraph(escape(fmt_config.get('institution', 'ACADEX LMS')), st_inst),
    ]
    if fmt_config.get('subtitle'):
        story.append(Paragraph(escape(fmt_config['subtitle']), st_sub))
    story.append(Paragraph(escape(exam.title), st_title))

    meta_data = [
        [
            Paragraph(f"<b>Class / Program:</b> {escape(exam.academic_class.name)}", st_meta),
            Paragraph(f"<b>Subject:</b> {escape(exam.subject.code)} - {escape(exam.subject.name)}", st_meta),
        ],
        [
            Paragraph(f"<b>Duration:</b> {exam.duration_minutes} Minutes", st_meta),
            Paragraph(f"<b>Maximum Marks:</b> {exam.total_marks}", st_meta),
        ],
    ]
    meta_table = Table(meta_data, colWidths=[85 * mm, 89 * mm])
    meta_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.75, colors.HexColor('#94a3b8')),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 6))

    instr_list = fmt_config.get('instructions') or ([exam.instructions] if exam.instructions else [])
    if instr_list:
        story.append(Paragraph("<b>General Instructions:</b>", st_meta))
        for line in instr_list:
            story.append(Paragraph(f"• {escape(line)}", st_opt))

    q_num = 1
    for sec in grouped:
        story.append(Paragraph(escape(sec['title']), st_sec))
        for q in sec['questions']:
            q_text = escape(latex_to_readable_math(q.prompt))
            story.append(Paragraph(f"<b>Q{q_num}.</b> {q_text} <b>[{q.marks} Marks]</b>", st_q))
            if q.question_type == Question.TYPE_MCQ:
                opts = [('A', q.option_a), ('B', q.option_b), ('C', q.option_c), ('D', q.option_d)]
                opt_str = ' &nbsp;&nbsp;&nbsp; '.join(
                    f"<b>({k})</b> {escape(latex_to_readable_math(v))}" for k, v in opts if v
                )
                story.append(Paragraph(opt_str, st_opt))
            elif q.question_type == Question.TYPE_FITB:
                story.append(Paragraph("Answer: ____________________________________________________", st_opt))
            else:
                story.append(Paragraph("_____________________________________________________________", st_opt))
                story.append(Paragraph("_____________________________________________________________", st_opt))
            q_num += 1

    story.append(Spacer(1, 10))
    story.append(Paragraph(f"<b>{escape(fmt_config.get('footer_note', '*** END OF QUESTION PAPER ***'))}</b>", st_sub))

    if include_answer_key:
        story.append(PageBreak())
        story.append(Paragraph(f"ANSWER KEY — {escape(exam.title)}", st_title))
        ans_num = 1
        for sec in grouped:
            for q in sec['questions']:
                ans_text = escape(latex_to_readable_math(q.get_correct_display()))
                story.append(Paragraph(f"<b>Q{ans_num}:</b> {ans_text}", st_q))
                ans_num += 1

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()
