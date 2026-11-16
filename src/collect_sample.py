#!/usr/bin/env python3
"""복지서비스 오픈API 샘플 50건(중앙 25 + 지자체 25) 상세 조회 후 '구비서류' 언급 여부 점검.
사용: .env에 DATA_GO_KR_KEY(Encoding 키)를 넣고 python3 src/collect_sample.py [중앙수] [지자체수]
출력: data/raw/*.xml, data/raw/samples.csv (data/raw는 커밋하지 않음). 요약은 표준출력.
"""
import csv, os, re, sys, time, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from pathlib import Path

from env import load_env

load_env()
OUT = Path(__file__).resolve().parent.parent / "data" / "raw"

KEY = os.environ.get("DATA_GO_KR_KEY")
if not KEY:
    sys.exit("DATA_GO_KR_KEY 환경변수에 공공데이터포털 인증키(Encoding)를 넣어 실행하세요.")
N_C, N_L = (int(a) for a in (sys.argv[1:3] + ["25", "25"])[:2])
BASE_C = "https://apis.data.go.kr/B554287/NationalWelfareInformationsV001"
BASE_L = "https://apis.data.go.kr/B554287/LocalGovernmentWelfareInformations"
DOC_RE = re.compile(r"구비서류|제출서류|필요서류|필요 서류|제출 서류|서류")
OUT.mkdir(parents=True, exist_ok=True)

def get(url, params, name):
    q = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params.items())
    full = f"{url}?serviceKey={KEY}&{q}"  # 키는 이미 인코딩된 값이라 그대로 붙임
    for i in range(3):
        try:
            raw = urllib.request.urlopen(urllib.request.Request(full, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read()
            break
        except urllib.error.HTTPError as e:
            raw = e.read()  # 게이트웨이는 키 오류도 XML 본문으로 알려줌
            break
        except Exception as e:
            if i == 2: raise RuntimeError(f"요청 실패 {name}: {type(e).__name__}") from None
            time.sleep(2)
    open(OUT / f"{name}.xml", "wb").write(raw)
    root = ET.fromstring(raw)
    err = root.find(".//errMsg")
    if err is not None: sys.exit(f"API 오류: {err.text} / {root.findtext('.//returnAuthMsg')}")
    return root

def txt(el, tag): return (el.findtext(tag) or "").strip()

rows = []
# 중앙부처
lst = get(f"{BASE_C}/NationalWelfarelistV001", {"callTp": "L", "pageNo": 1, "numOfRows": N_C, "srchKeyCode": "003"}, "central_list")
for s in lst.iter("servList"):
    sid = txt(s, "servId"); time.sleep(0.3)
    d = get(f"{BASE_C}/NationalWelfaredetailedV001", {"callTp": "D", "servId": sid}, f"central_{sid}")
    body = " ".join(txt(d, t) for t in ("tgtrDtlCn", "slctCritCn", "alwServCn", "wlfareInfoOutlCn"))
    apply = " | ".join(f"{txt(a,'servSeDetailNm')}({txt(a,'servSeDetailLink')})" for a in d.iter("applmetList"))
    forms = " | ".join(f"{txt(f,'basfrmNm') or ET.tostring(f, encoding='unicode')[:80]}" for f in d.iter("basfrmList"))
    rows.append(dict(src="중앙", servId=sid, servNm=txt(s, "servNm"), onapPsbltYn=txt(s, "onapPsbltYn"),
                     apply=apply[:300], forms=forms[:300], n_forms=len(list(d.iter("basfrmList"))),
                     docs_in_text=bool(DOC_RE.search(body + apply)), docs_snippet=(DOC_RE.search(body + apply) or [""])[0] and (body + apply)[max(0,(DOC_RE.search(body + apply).start()-40)):(DOC_RE.search(body + apply).start()+80)]))
# 지자체
lst = get(f"{BASE_L}/LcgvWelfarelist", {"pageNo": 1, "numOfRows": N_L}, "local_list")
for s in lst.iter("servList"):
    sid = txt(s, "servId"); time.sleep(0.3)
    d = get(f"{BASE_L}/LcgvWelfaredetailed", {"servId": sid}, f"local_{sid}")
    body = " ".join(txt(d, t) for t in ("sprtTrgtCn", "slctCritCn", "alwServCn", "aplyMtdCn"))
    reld = " | ".join(f"{txt(r,'wlfareInfoReldNm')}:{txt(r,'wlfareInfoReldCn')[:60]}" for r in d.iter("wlfareInfoReldList")) if d.find(".//wlfareInfoReldList") is not None else ""
    forms = " | ".join(ET.tostring(f, encoding="unicode")[:80] for f in d.iter("basfrmList"))
    m = DOC_RE.search(body + reld)
    rows.append(dict(src="지자체", servId=sid, servNm=txt(s, "servNm"), onapPsbltYn=txt(s, "aplyMtdNm"),
                     apply=txt(d, "aplyMtdCn")[:300], forms=forms[:300], n_forms=len(list(d.iter("basfrmList"))),
                     docs_in_text=bool(m), docs_snippet=(body + reld)[max(0, m.start()-40):m.start()+80] if m else ""))

with open(OUT / "samples.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

for src in ("중앙", "지자체"):
    r = [x for x in rows if x["src"] == src]
    if r:
        print(f"{src}: {len(r)}건 / 본문에 '서류' 언급 {sum(x['docs_in_text'] for x in r)}건 / 서식(basfrmList) 있는 건 {sum(x['n_forms']>0 for x in r)}건")
print(f"자세한 내용: {OUT}/samples.csv, 원본 XML: {OUT}/")
