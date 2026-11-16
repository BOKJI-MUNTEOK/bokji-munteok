"""복지서비스 API 상세 XML(중앙·지자체)을 같은 모양의 dict로 바꾼다."""
import html
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# data/raw(전체 수집본, 커밋 안 함)가 있으면 그것을, 없으면 커밋된 샘플 50건을 쓴다
RAW = ROOT / "data" / "raw" if any((ROOT / "data" / "raw").glob("*_list.xml")) else ROOT / "data" / "sample"


def clean(s: str | None) -> str:
    """API 본문은 '&amp;#9312;'처럼 두 번 인코딩돼 있고 줄바꿈이 \\r이다."""
    s = html.unescape(html.unescape(s or ""))
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t\u3000]+", " ", s)
    return "\n".join(line.strip() for line in s.split("\n") if line.strip())


def _t(el, tag):
    return clean(el.findtext(tag))


def parse_central(path: Path) -> dict:
    r = ET.parse(path).getroot()
    agencies = []  # applmetList: 신청·조사·결정·지급·이의신청 기관
    for a in r.iter("applmetList"):
        agencies.append({"stage": _t(a, "servSeDetailNm"), "desc": _t(a, "servSeDetailLink")})
    forms = [_t(f, "servSeDetailNm") for f in r.iter("basfrmList")]
    return {
        "src": "중앙",
        "servId": _t(r, "servId"),
        "servNm": _t(r, "servNm"),
        "org": _t(r, "jurMnofNm"),
        "target": _t(r, "tgtrDtlCn"),
        "criteria": _t(r, "slctCritCn"),
        "benefit": _t(r, "alwServCn"),
        "apply": "\n".join(f"{a['stage']}: {a['desc']}" for a in agencies),
        "summary": _t(r, "wlfareInfoOutlCn"),
        "agencies": agencies,
        "forms": sorted(set(forms)),
        "online_flag": None,  # 목록 응답(onapPsbltYn)에서 채운다
        "apply_method_name": "",
        "contact": _t(r, "rprsCtadr"),
    }


def parse_local(path: Path) -> dict:
    r = ET.parse(path).getroot()
    forms = [_t(f, "wlfareInfoReldNm") for f in r.iter("basfrmList")]
    region = " ".join(x for x in (_t(r, "ctpvNm"), _t(r, "sggNm")) if x)
    return {
        "src": "지자체",
        "servId": _t(r, "servId"),
        "servNm": _t(r, "servNm"),
        "org": _t(r, "bizChrDeptNm") or region,
        "target": _t(r, "sprtTrgtCn"),
        "criteria": _t(r, "slctCritCn"),
        "benefit": _t(r, "alwServCn"),
        "apply": _t(r, "aplyMtdCn"),
        "summary": _t(r, "servDgst"),
        "agencies": [],
        "forms": sorted(set(f for f in forms if f)),
        "online_flag": None,
        "apply_method_name": _t(r, "aplyMtdNm"),
        "contact": "; ".join(_t(c, "wlfareInfoReldCn") for c in r.iter("inqplCtadrList")),
    }


def _list_meta(path: Path) -> dict:
    """목록 응답에만 있는 값(중앙 onapPsbltYn, 지자체 aplyMtdNm, 지역)."""
    meta = {}
    if not path.exists():
        return meta
    for s in ET.parse(path).getroot().iter("servList"):
        meta[_t(s, "servId")] = {
            "onapPsbltYn": _t(s, "onapPsbltYn"),
            "aplyMtdNm": _t(s, "aplyMtdNm"),
            "region": " ".join(x for x in (_t(s, "ctpvNm"), _t(s, "sggNm")) if x),
        }
    return meta


def load_all(raw: Path = RAW) -> list[dict]:
    cmeta, lmeta = _list_meta(raw / "central_list.xml"), _list_meta(raw / "local_list.xml")
    out = []
    for p in sorted(raw.glob("central_*.xml")):
        if p.name == "central_list.xml":
            continue
        d = parse_central(p)
        m = cmeta.get(d["servId"], {})
        d["online_flag"] = {"Y": True, "N": False}.get(m.get("onapPsbltYn", ""))
        d["region"] = "전국"
        out.append(d)
    for p in sorted(raw.glob("local_*.xml")):
        if p.name == "local_list.xml":
            continue
        d = parse_local(p)
        m = lmeta.get(d["servId"], {})
        d["apply_method_name"] = d["apply_method_name"] or m.get("aplyMtdNm", "")
        d["region"] = m.get("region", "")
        out.append(d)
    return out


if __name__ == "__main__":
    rows = load_all()
    print(len(rows), "건")
    for r in rows[:3]:
        print(r["src"], r["servNm"], r["online_flag"], r["apply_method_name"], len(r["criteria"]))
