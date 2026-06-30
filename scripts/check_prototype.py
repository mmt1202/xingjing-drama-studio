from pathlib import Path

html = Path('prototype/index.html').read_text(encoding='utf-8')
css = Path('prototype/styles.css').read_text(encoding='utf-8')
required = ['星镜剧创', '标准生产流程', '分镜与故事板', '主体资产一致性', '导出前合规检查']
missing = [text for text in required if text not in html]
if missing:
    raise SystemExit(f'Missing required prototype labels: {missing}')
if '<link rel="stylesheet" href="styles.css" />' not in html:
    raise SystemExit('Stylesheet link missing')
if 'radial-gradient' not in css or '.hero-card' not in css:
    raise SystemExit('Expected high-fidelity visual styling not found')
print('Prototype structure check passed.')
