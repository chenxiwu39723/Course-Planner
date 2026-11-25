"""數學系計算與資料科學組113入學：可追溯、保守的畢業缺口檢查。"""
import argparse
import json
from pathlib import Path
BASE=Path(__file__).resolve().parents[2]/'data'/'graduation'  # 規則與案例集中放在 data/graduation/
VALID={'passed','in_progress','failed','withdrawn'}
def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))
def progress(required,done):
    return {'required':required,'completed':done,'remaining':max(0,required-done)}
def evaluate(rules,student):
    a=rules['applicability']
    if student.get('department')!=a['department'] or student.get('entry_year_roc')!=a['admission_academic_year_roc']:
        raise ValueError('僅支援113入學數學系(計算與資料科學組)主系')
    if not student.get('other_programs_excluded'):
        raise ValueError('尚未確認排除其他學程')
    rows=student['courses']; seen=set()
    for c in rows:
        key=(c['term'],c['course_id'])
        if key in seen or c['status'] not in VALID or not isinstance(c['credits'],(int,float)) or c['credits']<0:
            raise ValueError('重複紀錄、未知狀態或學分不合法')
        seen.add(key)
    # 有學分課依課號採通過優先；操行、體育、服務學習仍逐學期計次。
    passed={}
    for c in rows:
        if c['status']=='passed':
            if c['course_id'] in passed and c['credits']!=passed[c['course_id']]['credits']:
                raise ValueError('重修學分不一致，需人工核對')
            passed[c['course_id']]=c
    required=rules['department_required']['fixed_courses']
    fixed_done=sum(c['credits'] for c in required if c['course_id'] in passed)
    fixed={'credits':progress(47,fixed_done),'missing_courses':[c for c in required if c['course_id'] not in passed]}
    def category_sum(codes):
        return sum(c['credits'] for c in passed.values() if c['source_rule_code'] in codes)
    foreign=category_sum({'11201','11204','11205'})
    chinese=category_sum({'11401'})
    ge=category_sum({'11321','11322','11323','11324','11325'})
    common={'foreign_language':progress(6,min(6,foreign)),'chinese':progress(5,min(5,chinese)),'general_education':progress(14,min(14,ge)),'area_distribution':'待查'}
    cores={}
    for g in rules['core_electives']['choice_groups']:
        eligible={c['course_id']:c for c in rows if c['course_id'] in g['known_course_ids'] or c['source_rule_code']==g['source_rule_code']}
        done=[code for code in eligible if code in passed]
        cores[g['id']]={'courses':progress(g['required_course_count'],len(done)),'credits':progress(g['required_credits'],min(g['required_credits'],sum(passed[x]['credits'] for x in done))),'passed_course_ids':done,'in_progress_course_ids':[code for code,c in eligible.items() if code not in passed and c['status']=='in_progress'],'known_options':g['known_course_ids'],'option_catalog_complete':g['option_catalog_complete']}
    natural=[]
    for g in rules['natural_science']['series']:
        done=[c for c in passed.values() if c['course_id'] in g['known_course_ids'] or c['source_rule_code']==g['source_rule_code']]
        natural.append({'id':g['id'],'credits':progress(6,sum(c['credits'] for c in done)),'courses':progress(2,len(done)),'missing_known_course_ids':[x for x in g['known_course_ids'] if x not in passed]})
    dep=sum(c['credits'] for c in passed.values() if c.get('department_elective_eligible') is True)
    recognized=sum(c['credits'] for c in passed.values() if c.get('graduation_credit_eligible') is True)
    # 通識超修不能增加畢業總學分。
    recognized_ge=sum(c['credits'] for c in passed.values() if c.get('graduation_credit_eligible') is True and c['source_rule_code'] in {'11321','11322','11323','11324','11325'})
    recognized-=max(0,recognized_ge-14)
    nc={}
    for k,cat,target in [('freshman_pe','18201',2),('other_pe','18202',3),('service_learning','18401',2),('conduct','18101',8)]:
        nc[k]=progress(target,sum(c['status']=='passed' and c['source_rule_code']==cat for c in rows))
    nc['learning_passport']={'hours':student.get('learning_passport_hours'),'required_hours':None,'status':student.get('audit_categories',{}).get('18500',{}).get('status','unknown')}
    nc['english_proficiency']={'status':student.get('audit_categories',{}).get('19200',{}).get('status','unknown')}
    return {'rules_id':rules['rules_id'],'student_label':student.get('label'),'input_mode':student.get('source_mode'),'total_credits':progress(128,recognized),'passed_credits_pending_recognition':sum(c['credits'] for c in passed.values() if c.get('graduation_credit_eligible') is not True),'department_required':fixed,'common_required':common,'core_electives':cores,'natural_science':{'series':natural,'completed':any(g['credits']['remaining']==0 and g['courses']['remaining']==0 for g in natural)},'department_electives':{**progress(21,dep),'overlap_policy':'核心與系所選修的重疊採認待查，不加總缺額'},'noncredit_requirements':nc,'in_progress_credits':sum(c['credits'] for c in rows if c['status']=='in_progress'),'planning_overrides':student.get('planning_overrides',[]),'to_verify':rules['unresolved'],'graduation_verdict':'尚未完成；此為初步缺口檢查，非正式畢業資格審核'}
def main():
    p=argparse.ArgumentParser(); p.add_argument('--rules',default=BASE/'math_datascience_113_rules.json'); p.add_argument('--student',default=BASE/'math_datascience_113_demo.json'); p.add_argument('--output'); a=p.parse_args()
    txt=json.dumps(evaluate(read_json(a.rules),read_json(a.student)),ensure_ascii=False,indent=2)
    if a.output: Path(a.output).write_text(txt+'\n',encoding='utf-8')
    print(txt)
if __name__=='__main__': main()
