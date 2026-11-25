from pathlib import Path
import json,sys,subprocess,importlib.util
root=Path(__file__).resolve().parent


def check_sensor_position(cfg_text, expected, tol=0.01):
    """Compare the cfg's sensorPosition with config.json expected_sensor_position [height, azimuth tilt, elevation tilt].

    Returns a list of Chinese warnings (empty when they match). Read-only: never edits the TI cfg.
    TI cfg form: 'sensorPosition <height> <azimuthTilt> <elevationTilt>' (3 values); the
    5-value form '<x> <y> <height> <azimuthTilt> <elevationTilt>' is also accepted.
    """
    lines=[l.split('%',1)[0].strip() for l in cfg_text.splitlines()]
    lines=[l for l in lines if l.startswith('sensorPosition ')]
    if not lines:
        return ['警告：CFG 中没有 sensorPosition 行，无法核对雷达安装高度和倾角。']
    try:
        values=[float(v) for v in lines[-1].split()[1:]]
    except ValueError:
        return ['警告：无法读取 CFG 的 sensorPosition 行：'+lines[-1]]
    if len(values)==5:
        values=values[2:]
    if len(values)!=3:
        return ['警告：CFG 的 sensorPosition 格式无法识别：'+lines[-1]]
    if all(abs(a-b)<=tol for a,b in zip(values,expected)):
        return []
    names=['安装高度(米)','水平偏转(度)','下倾角(度)']
    diff='；'.join(n+' CFG='+('%g'%a)+' 期望='+('%g'%b) for n,a,b in zip(names,values,expected) if abs(a-b)>tol)
    return ['警告：CFG 的 sensorPosition 与 config.json 的 expected_sensor_position 不一致！'+diff,
            '高度和倾角不对时，雷达给出的目标高度会偏差，跌倒判断会不准。请核对实际安装，并人工修改 CFG 或 config.json（本程序不会修改 TI 的 CFG 文件）。']


def main():
    c=json.loads((root/'config.json').read_text(encoding='utf-8-sig'))
    for m in ['serial','numpy']:
        if importlib.util.find_spec(m) is None:
            raise SystemExit('Missing '+m+'. Use this Python: '+sys.executable+' -m pip install -r requirements.txt')
    common=Path(c['toolbox_root'])/'tools/visualizers/Applications_Visualizer/common/parseFrame.py'
    if not common.is_file(): raise SystemExit('TI parser missing. Correct toolbox_root in config.json: '+str(common))
    p=Path(c['cfg_path'])
    if not p.is_file(): raise SystemExit('CFG missing: '+str(p))
    text=p.read_text(encoding='utf-8-sig')
    for line in text.splitlines():
        if line.strip().startswith('sensorPosition '): print('CFG installation:',line,flush=True)
    expected=c.get('expected_sensor_position',[1.6,0,15])
    print('Expected installation (config.json expected_sensor_position): height %g m, azimuth %g deg, %g deg down.'%tuple(expected),flush=True)
    warnings=check_sensor_position(text,expected)
    for w in warnings: print(w,flush=True)
    if not warnings: print('sensorPosition 与期望安装一致。仍请确认实际安装高度和倾角。',flush=True)
    print('Close Visualizer and UniFlash. Backend will own COM ports.',flush=True)
    print('Website: http://127.0.0.1:'+str(c['http_port'])+'/',flush=True)
    subprocess.run([sys.executable,str(root/'service.py')],cwd=str(root),check=True)


if __name__=='__main__':
    main()
