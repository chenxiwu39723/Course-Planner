import copy
import unittest
from check_graduation import BASE,read_json,evaluate
class TestGraduation(unittest.TestCase):
    def setUp(self):
        self.r=read_json(BASE/'math_datascience_113_rules.json')
        self.s=read_json(BASE/'math_datascience_113_demo.json')
    def test_planning_version(self):
        p=evaluate(self.r,self.s)
        self.assertEqual(p['total_credits']['completed'],77)
        self.assertEqual(p['total_credits']['remaining'],51)
        self.assertEqual(p['department_required']['credits']['remaining'],0)
    def test_pending_not_completed_and_project_not_confused(self):
        r=evaluate(self.r,self.s)
        self.assertEqual(r['in_progress_credits'],24)
        self.assertEqual(r['core_electives']['core_intro']['credits']['completed'],0)
        self.assertEqual(r['core_electives']['core_project']['credits']['remaining'],3)
        self.assertEqual(r['department_electives']['remaining'],15)
        self.assertEqual(r['common_required']['general_education']['remaining'],4)
        self.assertEqual(r['noncredit_requirements']['conduct']['completed'],4)
        self.assertEqual(r['noncredit_requirements']['other_pe']['remaining'],3)
    def test_retake_once_and_noncredit_by_term(self):
        c=copy.deepcopy(next(c for c in self.s['courses'] if c['course_id']=='MA1001'))
        c['term']='1152'; self.s['courses'].append(c)
        self.assertEqual(evaluate(self.r,self.s)['total_credits']['completed'],77)
    def test_duplicate_and_wrong_year_rejected(self):
        self.s['courses'].append(self.s['courses'][0])
        with self.assertRaises(ValueError): evaluate(self.r,self.s)
        self.s=read_json(BASE/'math_datascience_113_demo.json'); self.s['entry_year_roc']=114
        with self.assertRaises(ValueError): evaluate(self.r,self.s)
    def test_mixed_science_does_not_complete_series(self):
        self.s['courses'].append({'term':'1152','course_id':'CHTEST','credits':3,'status':'passed','source_rule_code':'13202','graduation_credit_eligible':True})
        self.assertFalse(evaluate(self.r,self.s)['natural_science']['completed'])
    def test_new_passed_elective_not_automatically_recognized(self):
        c=next(c for c in self.s['courses'] if c['course_id']=='MA6091'); c['status']='passed'
        r=evaluate(self.r,self.s)
        self.assertEqual(r['total_credits']['completed'],77)
        self.assertEqual(r['passed_credits_pending_recognition'],3)
        self.assertEqual(r['department_electives']['completed'],6)
    def test_excess_ge_excluded(self):
        self.s['courses'].append({'term':'1152','course_id':'GE_TEST','credits':6,'status':'passed','source_rule_code':'11321','graduation_credit_eligible':True})
        self.assertEqual(evaluate(self.r,self.s)['total_credits']['completed'],81)
if __name__=='__main__': unittest.main()
