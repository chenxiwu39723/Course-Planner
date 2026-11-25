"""文字型中央大學畢業審核 PDF：只匯入主系，不保留姓名、學號、分數。"""
import argparse
import json
import re
from pathlib import Path
import fitz

COURSE = re.compile(r'^\s*(?P<term>\d{4})\s+(?P<code>[A-Z]{2}\d{4})\s+(?P<name>.+?)\s+(?P<credits>\d+)\s+(?P<rest>.+)$')
CATEGORY = re.compile(r'^\s*(完成|未完成)\s+(\d{5})\s*-\s*(.*)')

def parse_text(text):
    start = re.search(r'10000\s*-\s*主系', text)
    if not start:
        raise ValueError('找不到 10000 - 主系')
    header = re.findall(r'(\d{3})-\d+-\d+-\d+-([^\n]+)', text[:start.start()])
    if not header:
        raise ValueError('無法確認入學年度與主系')
    year, department = header[-1]
    body = text[start.end():]
    # 同頁或跨頁的其他學程均停止；不把師培與雙主修算進主系。
    boundary = re.search(r'^\s*(?:[3-9]0000\s*-|\d{3}-\d+-\d+-\d+-)', body, re.M)
    if boundary:
        body = body[:boundary.start()]
    total = re.search(r'(\d+)學分[，,]\s*(\d+)門課[，,]\s*應修(\d+)學分', body)
    if not total:
        raise ValueError('無法確認主系學分合計')
    categories = {}
    # 將跨行類別標題與門檻併在一起。
    matches = list(re.finditer(r'^\s*(完成|未完成)\s+(\d{5})\s*-', body, re.M))
    for i, m in enumerate(matches):
        chunk = body[m.end():matches[i+1].start() if i+1 < len(matches) else len(body)]
        q = re.search(r'應修學分[：:]\s*(\d+).*?應修科目[：:]\s*(\d+).*?實修學分[：:]\s*(\d+).*?實修科目[：:]\s*(\d+)', chunk, re.S)
        categories[m[2]] = {'status':'passed' if m[1]=='完成' else 'not_completed'}
        if q:
            categories[m[2]].update(dict(zip(('required_credits','required_count','passed_credits','passed_count'),map(int,q.groups()))))
    category = None
    courses = {}
    for line in body.splitlines():
        m = CATEGORY.match(line)
        if m:
            category = m[2]
        c = COURSE.match(line)
        if not c:
            continue
        rest = c['rest']
        if '未到' in rest:
            status = 'in_progress'
        elif '停修' in rest:
            status = 'withdrawn'
        elif '未通過' in rest:  # 必須在「通過」之前檢查
            status = 'failed'
        elif '通過' in rest:
            status = 'passed'
        else:
            raise ValueError(f'{c["code"]} 狀態無法判斷')
        key = (c['term'], c['code'])
        row = {'term':c['term'],'course_id':c['code'],'name':c['name'].strip(), 'credits':int(c['credits']), 'status':status,'source_rule_code':category}
        if key in courses:
            if courses[key]['status'] != status:
                raise ValueError(f'{c["code"]} 同學期狀態衝突')
        else:
            courses[key] = row
    if not courses:
        raise ValueError('沒有課程，請確認是文字型 PDF')
    passport = re.search(r'總時數[：:]\s*([\d.]+)',body)
    return {'document_type':'ncu_main_major_audit_import','entry_year_roc':int(year),'department':department.strip(), 'main_program_total':{'completed_credits_as_reported':int(total[1]),'completed_course_count_as_reported':int(total[2]),'required_credits':int(total[3])},'student_record_is_anonymized':True,'other_programs_excluded':True,'courses':list(courses.values()),'audit_categories':categories,'learning_passport_hours':float(passport[1]) if passport else None}

def parse_pdf(path):
    with fitz.open(path) as doc:
        text = '\n'.join(p.get_text(sort=True) for p in doc)
    return parse_text(text)

def main():
    p = argparse.ArgumentParser()
    p.add_argument('pdf')
    p.add_argument('-o','--output',default='main_major_courses.json')
    a = p.parse_args()
    Path(a.output).write_text(json.dumps(parse_pdf(a.pdf),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('已輸出去識別化主系資料：',a.output)
if __name__=='__main__': main()
