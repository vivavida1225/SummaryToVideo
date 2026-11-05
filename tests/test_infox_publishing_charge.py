from backend.infox.publishing.manager import charge_summary


def test_observed_will_charge_schema_and_allowlisted_usage():
    assert charge_summary({'will_charge': False, 'credits': 0, 'duration_sec': 61,
        'last_charged_at':'private', 'account':'SECRET'}) == {
            'available':True,'charged':False,'credits':0,'duration_seconds':61}
    assert charge_summary({'will_charge':True,'credits':float('nan')}) == {'available':True,'charged':True}
    assert charge_summary({'account':'SECRET'}) == {'available':False,'charged':None}
