"""與選課助手相同入口：PDF匯入、適用年度驗證、JSON缺口報告。"""
import argparse
import copy
import json
from pathlib import Path
from import_graduation_pdf import parse_pdf
from check_graduation import BASE,read_json,evaluate

def pdf_to_student(parsed,statistics_completed=True):
    if parsed['department']!='數學系(計算與資料科學組)' or parsed['entry_year_roc']!=113:
        raise ValueError('僅支援113入學數學系(計算與資料科學組)主系')
    s=copy.deepcopy(parsed)
    if not s.get('other_programs_excluded'):
        raise ValueError('未排除其他學程')
    s.update(label='匿名PDF匯入案例',program='計算與資料科學組',source_mode='actual_pdf')
    unique={c['course_id']:c for c in s['courses'] if c['status']=='passed'}
    total=sum(c['credits'] for c in unique.values())
    # 此模組未能解釋抵免、超修、重修等差異時，不擅自輸出可採計學分。
    if total!=s['main_program_total']['completed_credits_as_reported']:
        raise ValueError('課程加總與PDF主系合計不同，需人工核對採認，停止自動計算')
    for c in s['courses']:
        c['graduation_credit_eligible']=True if c['status']=='passed' else None
        c['department_elective_eligible']=None
    # 只有PDF明載的19120認列6學分，且14101內MA課恰為6學分，才帶入個案認列。
    electives=[c for c in s['courses'] if c['status']=='passed' and c['source_rule_code']=='14101' and c['course_id'].startswith('MA')]
    reported=s.get('audit_categories',{}).get('19120',{}).get('passed_credits')
    if reported is not None and sum(c['credits'] for c in electives)==reported:
        for c in electives: c['department_elective_eligible']=True
    if statistics_completed:
        targets=[c for c in s['courses'] if c['course_id']=='MA2011']
        if len(targets)!=1:
            raise ValueError('統計學紀錄不是唯一一筆，不能自動套用規劃假設')
        c=targets[0]
        s['planning_overrides']=[{'course_id':'MA2011','source_status':c['status'],'planning_status':'passed','reason':'命令列明確指定統計學完成規劃情境'}]
        c['source_status']=c['status']; c['status']='passed'; c['graduation_credit_eligible']=True
        s['source_mode']='planning_override'
    return s

def analyze_pdf(pdf_path,statistics_completed=True):
    return evaluate(read_json(BASE/'math_datascience_113_rules.json'),pdf_to_student(parse_pdf(pdf_path),statistics_completed))

def main():
    p=argparse.ArgumentParser()
    p.add_argument('pdf',nargs='?'); p.add_argument('--demo',action='store_true'); p.add_argument('--output')
    a=p.parse_args()
    if bool(a.pdf)==a.demo: p.error('提供PDF或--demo，二選一')
    if a.demo:
        report=evaluate(read_json(BASE/'math_datascience_113_rules.json'),read_json(BASE/'student_demo.json'))
    else: report=analyze_pdf(a.pdf)
    txt=json.dumps(report,ensure_ascii=False,indent=2)
    if a.output: Path(a.output).write_text(txt+'\n',encoding='utf-8')
    print(txt)
if __name__=='__main__': main()
