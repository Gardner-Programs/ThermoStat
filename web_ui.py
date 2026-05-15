# HTML is stored in _web_p1.html / _web_p2.html on flash.
# Only this tiny dynamic block is built at runtime per request.

def get_init(target, ac_mode, ac_fan, ac_power):
    js_power = "true" if ac_power else "false"
    return (
        "var curTarget=" + str(round(target, 1)) + ";"
        "var curMode='" + ac_mode + "';"
        "var curFan='" + ac_fan + "';"
        "var curPower=" + js_power + ";"
        "try{var _ls=JSON.parse(localStorage.getItem('acCtrl')||'{}');"
        "if(_ls.mode)curMode=_ls.mode;"
        "if(_ls.fan)curFan=_ls.fan;"
        "if(_ls.power!==undefined)curPower=_ls.power;}catch(e){}"
        "var schedData={'home_temp':72,'windows':[]};"
        "var tempTimer=null;"
        "var editingTemp=false;"
        "var ctrlDirty=false;"
        "var histData=[];var histView='12h';"
    )
