"""복지서비스 API 상세 XML(중앙·지자체)을 같은 모양의 dict로 바꾼다."""
import html
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 파일 기반 검증 도구의 기본 경로. 운영 앱은 welfare_api를 통해 실제 API 목록을 쓴다.
RAW = ROOT / "data" / "raw" if any((ROOT / "data" / "raw").glob("*_list.xml")) else ROOT / "data" / "sample"


def clean(s: str | None) -> str:
    """API 본문은 '&amp;#9312;'처럼 두 번 인코딩돼 있고 줄바꿈이 \\r이다."""
    s = html.unescape(html.unescape(s or ""))
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t\u3000]+", " ", s)
    return "\n".join(line.strip() for line in s.split("\n") if line.strip())


def _t(el, tag):
    return clean(el.findtext(tag))


def parse_central(path: Path | ET.Element) -> dict:
    r = path if isinstance(path, ET.Element) else ET.parse(path).getroot()
    agencies = []  # applmetList: 신청·조사·결정·지급·이의신청 기관
    for a in r.iter("applmetList"):
        agencies.append({"stage": _t(a, "servSeDetailNm"), "desc": _t(a, "servSeDetailLink")})
    forms = [_t(f, "servSeDetailNm") for f in r.iter("basfrmList")]
    files = [{"name": _t(f, "servSeDetailNm"), "url": _t(f, "servSeDetailLink")} for f in r.iter("basfrmList")]
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
        "form_files": files,  # 첨부 양식·공고문 내려받기 주소(복지로)
        "online_flag": None,  # 목록 응답(onapPsbltYn)에서 채운다
        "apply_method_name": "",
        "contact": _t(r, "rprsCtadr"),
    }


def parse_local(path: Path | ET.Element) -> dict:
    r = path if isinstance(path, ET.Element) else ET.parse(path).getroot()
    forms = [_t(f, "wlfareInfoReldNm") for f in r.iter("basfrmList")]
    files = [{"name": _t(f, "wlfareInfoReldNm"), "url": _t(f, "wlfareInfoReldCn")} for f in r.iter("basfrmList")]
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
        "form_files": [f for f in files if f["name"] and f["url"]],
        "online_flag": None,
        "apply_method_name": _t(r, "aplyMtdNm"),
        "contact": "; ".join(_t(c, "wlfareInfoReldCn") for c in r.iter("inqplCtadrList")),
    }


def _tags(el, *tags) -> list[str]:
    """'청년, 노년' 같은 쉼표 목록. 중앙은 lifeArray, 지자체는 lifeNmArray처럼 이름이 다르다."""
    text = ",".join(_t(el, t) for t in tags)
    return [x.replace(" · ", "·").strip() for x in text.split(",") if x.strip()]


def _list_meta(path: Path) -> dict:
    """목록 응답에만 있는 값(중앙 onapPsbltYn, 지자체 aplyMtdNm, 지역, 생애주기·대상·주제 태그)."""
    meta = {}
    if not path.exists():
        return meta
    for s in ET.parse(path).getroot().iter("servList"):
        meta[_t(s, "servId")] = {
            "onapPsbltYn": _t(s, "onapPsbltYn"),
            "aplyMtdNm": _t(s, "aplyMtdNm"),
            "region": " ".join(x for x in (_t(s, "ctpvNm"), _t(s, "sggNm")) if x),
            "sido": _t(s, "ctpvNm"),
            "sgg": _t(s, "sggNm"),
            "life": _tags(s, "lifeArray", "lifeNmArray"),
            "groups": _tags(s, "trgterIndvdlArray", "trgterIndvdlNmArray"),
            "themes": _tags(s, "intrsThemaArray", "intrsThemaNmArray"),
            "link": _t(s, "servDtlLink"),
        }
    return meta


def _add_tags(d: dict, m: dict) -> None:
    for k in ("sido", "sgg", "link"):
        d[k] = m.get(k, "")
    for k in ("life", "groups", "themes"):
        d[k] = m.get(k, [])


def parse_list_item(item: ET.Element, source: str) -> dict:
    """목록은 검색용이다. 상세를 받기 전의 빈 필드로 점수를 계산하지 않는다."""
    central = source == "central"
    d = (parse_central if central else parse_local)(item)
    d.update(
        api_source=source, detail_loaded=False,
        summary=_t(item, "servDgst"),
        sido=_t(item, "ctpvNm"), sgg=_t(item, "sggNm"),
        region="전국" if central else " ".join(filter(None, (_t(item, "ctpvNm"), _t(item, "sggNm")))),
        life=_tags(item, "lifeArray", "lifeNmArray"),
        groups=_tags(item, "trgterIndvdlArray", "trgterIndvdlNmArray"),
        themes=_tags(item, "intrsThemaArray", "intrsThemaNmArray"),
        link=_t(item, "servDtlLink"),
        online_flag={"Y": True, "N": False}.get(_t(item, "onapPsbltYn")),
        last_modified=_t(item, "lastModYmd"),
    )
    # 조회수는 방문할 때마다 변하므로 제외하고 나머지 원본 목록 필드는 모두 비교한다.
    stable = sorted((child.tag, clean(child.text)) for child in item if child.tag != "inqNum")
    d["list_hash"] = hashlib.sha256(json.dumps(stable, ensure_ascii=False).encode()).hexdigest()
    return d


def merge_detail(item: dict, root: ET.Element) -> dict:
    """상세 본문과 목록 전용 필드를 합친다. 목록에서 누락된 값은 상세로 보충한다."""
    detail = (parse_central if item["api_source"] == "central" else parse_local)(root)
    merged = {**item, **detail, "detail_loaded": True}
    for field in ("servNm", "org", "summary", "apply_method_name", "contact"):
        merged[field] = detail[field] or item[field]
    merged["online_flag"] = item["online_flag"]
    return merged


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
        _add_tags(d, m)
        out.append(d)
    for p in sorted(raw.glob("local_*.xml")):
        if p.name == "local_list.xml":
            continue
        d = parse_local(p)
        m = lmeta.get(d["servId"], {})
        d["apply_method_name"] = d["apply_method_name"] or m.get("aplyMtdNm", "")
        d["region"] = m.get("region", "")
        _add_tags(d, m)
        out.append(d)
    return out


if __name__ == "__main__":
    rows = load_all()
    print(len(rows), "건")
    for r in rows[:3]:
        print(r["src"], r["servNm"], r["online_flag"], r["apply_method_name"], len(r["criteria"]))
