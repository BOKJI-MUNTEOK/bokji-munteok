"""공통 서식 필드 매칭 규칙 (.hwpx / .pdf / .hwp 공용).

보수적 원칙 (2026-11-19 갱신):
1. 서명·(인)·날인·도장 칸만 비워 둔다. 주민등록번호·계좌·월소득은 채운다.
2. 구역(applicant vs proxy vs excluded)이 명확해야 하며 혼동 시 건너뛴다.
3. 이미 문자가 들어 있는 셀은 덮어쓰지 않는다.
4. 확신이 없는 라벨은 건너뛴다(오답 기입보다 빈칸이 낫다).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# 서명·날인 차단 목록 (반드시 비워 둠)
SENSITIVE_RE = re.compile(r"서명|\(인\)|날인|도장|서명\s*또는\s*인|㉿|㊞|\U000f012b")

# 기관 장부·접수대장·명부·결재 헤더 (표/쪽 단위 전체 제외)
LEDGER_TITLE_RE = re.compile(
    r"(?:접수|관리|발급|지급|지원|신청|등록|처리|확인|상담|방문|근무|출석|운영|현황)?\s*대장"
    r"(?!암|내시경|질환|용종)|대장부|(?:지급|대상자|신청자|수급자|수혜자)?\s*명부|처리부"
)
APPROVAL_HEADER_RE = re.compile(r"(?:^|\s)결\s*재(?:\s|$)|결재란")

# 1순위 사망/고인 구역 (신청인 병기라도 반드시 제외)
DECEASED_SECTION_RE = re.compile(
    r"사망(?:한|자|인)|(?<![가-힣])사망(?![가-힣])|(?<![가-힣])망인(?![가-힣])|(?<![가-힣])고인(?![가-힣])"
)
# 세로 병합 구역 첫 행 라벨에서 사망자 관련 구역 판별용 (사망일 포함)
DECEASED_FIRST_ROW_RE = re.compile(
    r"사망(?:한|자|인|일)|(?<![가-힣])사망(?![가-힣])|(?<![가-힣])망인(?![가-힣])|(?<![가-힣])고인(?![가-힣])"
)

# 다른 사람 유형 구역 (낱말 전체 기준 매칭, skipped reason="other_person")
OTHER_PERSON_SECTION_RE = re.compile(
    r"(?<![가-힣])(?:"
    r"출산자|임산부|산모|출생자|유족|배우자|참전유공자|국가유공자|"
    r"학생|대상학생|피보호자|수혜자|수혜아동|아동|자녀|영유아|피보험자|"
    r"보증인|연대보증인|인우보증|추천인|"
    r"세대원|가구원|동거인|가족(?!관계증명)|부양의무자|직계존속|"
    r"미성년자|법정대리인|친권자|후견인|어르신"
    r")(?![가-힣])|"
    r"학생\s*인적사항|보훈\s*대상자|가입\s*선원|권\s*유\s*자|세대\s*구성원|"
    r"가족\s*(?:상황|사항|구성|현황)|부양\s*가족|동거\s*가족|동행(?:\s*가족|\s*인)?|"
    r"동순위\s*수급권자|대표자\s*선정|"
    r"(?:지원|등록|융자|수혜|보호|지급|급여|서비스|봉양)?\s*대상자(?!(?:\s*와의\s*관계|\s*와\s*관계))|"
    r"수급\s*권자(?!(?:\s*와의\s*관계|\s*와\s*관계))"
)

# 기입 제외 구역 (담당자, 기관·공무원, 결재, 장부·명부 등)
EXCLUDED_SECTION_RE = re.compile(
    r"담당자|담당\s*공무원|담당\s*직원|처리기관|확인자|접수\s*담당|작성자\s*\(공무원\)|"
    r"요양기관|진단기관|진료기관|읍\s*[·ㆍ․.]?\s*면장|읍면동\s*담당|동장|기관\s*확인|직인|"
    r"처리\s*(?:기관|부서|담당|결과|내용|절차|의견|현황)|접수\s*(?:기관|담당|처|부서|확인)|"
    r"신청인이\s*기재하지\s*않|지원대상\s*확인|지급대상자\s*명부|대상자\s*명부|신청자\s*명부|"
    r"추천기관|결재|확인란|"
    r"사업장|근무처|직장정보|입소시설|치료기관|의료기관|학교정보|"
    + LEDGER_TITLE_RE.pattern
)

# 인라인 날짜/서명란 차단 문맥 (공무원·확인자·보증인 구역)
OFFICIAL_INLINE_CTX_RE = re.compile(
    r"확인자|담당\s*공무원\s*(?:확인(?!사항)|서명|성명|직)|담당자\s*(?:직|성명|서명|확인)|"
    r"읍\s*[·ㆍ․.]?\s*면장|읍면동\s*담당|동장|직인|결재|활동보조인|"
    r"보증합니다|보증인|신청인이\s*기재하지\s*않"
)

# 표 헤더 아래 행 기입 시 제외할 행 머리말 (부부 분리행·가구원)
EXCLUDED_ROW_HEAD_RE = re.compile(
    r"^(?:여성|남성|배우자|부|모|자녀|동거인|세대원|가구원|직계존속)$"
)

# 대리인 구역
PROXY_SECTION_RE = re.compile(
    r"대리\s*인|대리\s*수령인|대리\s*신청인|대리\s*수령|대리자|"
    r"위임받는\s*사람|위임\s*받는|수임인|대신\s*신청|대리\s*신청|본인이\s*신청할\s*경우\s*생략"
)

# 신청인(본인) 구역 및 괄호 병기('(대리신청인)' 포함)
APPLICANT_MIXED_RE = re.compile(
    r"(?:신청인|신청자|신고인|청구인)\s*\(\s*(?:대리\s*신청인|대리인|보호자|신청인|본인|청구인)\s*\)"
)
APPLICANT_SECTION_RE = re.compile(
    r"신청인|신청자|본인|신고\s*\(\s*신청\s*\)\s*인|신고인|청구인|(?<!피)보호자|"
    r"위임하는\s*사람|위임한\s*사람|위임인|수급자|이용자|가입자|참여자|대출신청인|교육생"
)

# 타인/대리 구역 뒤에서 신청인 계좌 입력으로 복귀하는 행 머리 & 현행/변경 쌍
ACCOUNT_SECTION_RE = re.compile(
    r"^(?:[가-힣]*?(?:예금|지급|수령|입금|지원))?계좌(?:변경신청내역|정보|번호|내역)?$"
)
OLD_BLOCK_HEAD_RE = re.compile(r"^(?:현행|기존|변경전|종전)(?:계좌|정보|내용)?$")
NEW_BLOCK_HEAD_RE = re.compile(r"^(?:변경|신규|변경후)(?:계좌|정보|내용)?$")

# 서식/대장 제목 판별
FORM_TITLE_RE = re.compile(r"(?:신청서|신고서|청구서|동의서|요청서|서약서)(?:\s*\([^)]*\))?\s*$")

# 라벨 제외 키워드 (공백·특수문자 제거 후 normalize_label 기준으로 검사)
NON_TARGET_LABEL_RE = re.compile(
    r"사업명|(?<!금융)기관명|시설명|단체명|법인명|학교명|병원명|서비스명|계좌명|상품명|질환명|직장명|"
    r"담당부서|문의처|제출처|기관연락처|접수처|대표자성명|사업주|사업장|"
    r"여성휴대폰|남성휴대폰|성명(?:여성|남성)|여성주소|남성주소|"
    r"지급일|시행일|접수일|발급일|입금일|결재일|만료일|계약일|출산|사망일|혼인일|전입일|퇴직일|"
    r"입사연월일|성립일|발생일|감소일|개시일|납부일|지정일|"
    r"연간소득|연소득|보수총액|이전월소득|대상월|"
    r"우편번호|이메일|전자우편|팩스|FAX|홈페이지|비고|참고|안내|유의사항"
)


def display_width_spaces(text: str) -> int:
    """공백 1칸=1 기준으로 텍스트 표시 폭을 계산한다(한글·전각=2, 숫자·영문·반각=1)."""
    return sum(2 if ord(ch) > 0x7F else 1 for ch in (text or ""))


def slot_width_spaces(text: str) -> int:
    """공백·밑줄 자리표시 문자열의 표시 폭을 계산한다(전각 공백 \\u3000=2, 그 외=1)."""
    return sum(2 if ch == "\u3000" else 1 for ch in (text or ""))


@dataclass
class MatchDecision:
    action: str  # "fill" | "sensitive_blank" | "skip"
    field_key: str | None = None
    value: str | None = None
    reason: str = ""
    section: str = "unknown"


def normalize_label(text: str) -> str:
    s = re.sub(r"[\s\u3000]+", "", text or "")
    s = re.sub(r"[*※①②③④⑤⑥⑦⑧⑨⑩•·․ㆍ.:\[\]()（）<>「」『』\-_/]", "", s)
    return s


def is_cell_empty(text: str) -> bool:
    """셀이 기입 가능한 빈 상태인지 판별한다. 단일 '-' 구분선(주민번호 앞뒤 등)은 비어있지 않은 것으로 본다."""
    t = re.sub(r"[\s\u3000\r\n]+", "", text or "")
    if not t:
        return True
    if t == "-":
        return False
    if re.fullmatch(r"[_\s·…]+", t):
        return True
    return False


def detect_section_context(text: str, current: str = "applicant") -> str:
    """표/행 문맥에서 현재 구역을 판별한다 (excluded / other_person / proxy / applicant)."""
    if not text:
        return current
    compact = re.sub(r"\s+", " ", text).strip()
    if LEDGER_TITLE_RE.search(compact) or LEDGER_TITLE_RE.search(re.sub(r"\s+", "", compact)):
        return "excluded"

    # 신청인 확인/서명 문단 안에 붙은 주의사항·법령 인용문은 구역 헤더로 오인하지 않음
    compact_for_sec = re.sub(r"※\s*만\s*14세[^.\n]*법정대리인[^.\n]*\.?", "", compact).strip()
    compact_for_sec = re.sub(r"담당\s*공무원으로부터\s*안내[^.\n]*\.?", "", compact_for_sec).strip()
    compact_no_law = re.sub(r"「[^」]*」|『[^』]*』", "", compact_for_sec).strip() or compact_for_sec
    compact_ns = re.sub(r"\s+", "", compact_no_law)

    if EXCLUDED_SECTION_RE.search(compact_no_law) or EXCLUDED_SECTION_RE.search(compact_ns):
        return "excluded"
    if compact_for_sec.startswith("※"):
        return current
    if compact_for_sec.count("□") >= 2 or len(re.findall(r"\(\s{2,}\)", text)) >= 2:
        return current
    if len(compact_ns) <= 80 and FORM_TITLE_RE.search(compact_no_law) and "인우보증" not in compact_ns:
        return current
    if (
        re.search(r"(?:신청|신고|청구|동의)합니다", compact_ns)
        and not OFFICIAL_INLINE_CTX_RE.search(compact_no_law)
        and "인우보증" not in compact_ns
    ):
        return "applicant"

    # '~와의 관계' 같은 관계 라벨 자체는 구역 헤더가 아님
    rel_check = re.sub(r"\s+", "", compact_for_sec)
    if re.fullmatch(
        r"(?:\(?[①-⑩*]?\s*)?(?:보훈대상자|지원대상자|지급대상자|수급권자|출산자|신청인|세대주|임산부|본인|장애인|사망자|사망인|고인|망인)와의?관계[:：)]*",
        rel_check,
    ):
        return current

    # 우선순위: ① 사망/사망인/사망자/사망한/망인/고인 → ②a 신청인(대리신청인) → ②b 대리인 → ②c 신청인/보호자 → ③ 타인 유형
    if DECEASED_SECTION_RE.search(compact_no_law) or DECEASED_SECTION_RE.search(compact_ns):
        return "other_person"
    if APPLICANT_MIXED_RE.search(compact_for_sec) or APPLICANT_MIXED_RE.search(compact_ns):
        return "applicant"
    if PROXY_SECTION_RE.search(compact_for_sec) or PROXY_SECTION_RE.search(compact_ns):
        return "proxy"
    if APPLICANT_SECTION_RE.search(compact_for_sec) or APPLICANT_SECTION_RE.search(compact_ns):
        return "applicant"
    if OTHER_PERSON_SECTION_RE.search(compact_no_law) or OTHER_PERSON_SECTION_RE.search(compact_ns):
        return "other_person"
    return current


def is_deceased_band_first_row(row_texts: list[str]) -> bool:
    """세로 병합 구역의 첫 행(col>=1) 라벨에 사망자·사망일·망인·고인이 있으면 True('~와의 관계'·체크박스 행 제외)."""
    for txt in row_texts:
        if not txt:
            continue
        t = re.sub(r"\s+", " ", txt).strip()
        if not t or len(t) > 40 or t.startswith("※") or t.count("□") >= 2:
            continue
        t_ns = re.sub(r"\s+", "", t)
        if re.search(r"와의?관계", t_ns):
            continue
        if DECEASED_FIRST_ROW_RE.search(t) or DECEASED_FIRST_ROW_RE.search(t_ns):
            return True
    return False


def is_ledger_or_approval_table(top_cell_texts: list[str], prev_title: str = "") -> bool:
    """쪽/표 제목의 대장·명부 키워드 또는 표 상단 머리(row<=3)의 결재 헤더 여부를 판별한다."""
    if prev_title:
        pt_ns = re.sub(r"\s+", "", prev_title)
        if LEDGER_TITLE_RE.search(prev_title) or LEDGER_TITLE_RE.search(pt_ns):
            return True
    for txt in top_cell_texts:
        if not txt:
            continue
        t_clean = re.sub(r"\s+", " ", txt).strip()
        t_ns = re.sub(r"\s+", "", t_clean)
        if LEDGER_TITLE_RE.search(t_clean) or LEDGER_TITLE_RE.search(t_ns):
            return True
        if APPROVAL_HEADER_RE.search(t_clean) or re.fullmatch(r"결재|결재란", t_ns):
            return True
    return False


def _est_cell_text_width(text: str, pt100: int, spacing: int, ratio_eff: float) -> float:
    units = 0.0
    for ch in text:
        if ch == " ":
            units += 0.35
        elif ord(ch) > 0x7F:
            units += 1.0
        else:
            units += 0.60
    sp_factor = max(0.85, 1.0 + spacing / 100.0)
    return units * pt100 * ratio_eff * sp_factor * 1.05


def _wrapped_line_count(text: str, avail_w: float, pt100: int, spacing: int, ratio_eff: float) -> int:
    tokens = re.findall(r"\S+\s*", text) or list(text)
    lines = 1
    cur = ""
    for tok in tokens:
        cand = cur + tok
        if not cur or _est_cell_text_width(cand.rstrip(), pt100, spacing, ratio_eff) <= avail_w:
            cur = cand
        else:
            lines += 1
            cur = tok.lstrip()
            if _est_cell_text_width(cur.rstrip(), pt100, spacing, ratio_eff) > avail_w:
                return 99
    return lines


def fit_text_to_cell(
    text: str,
    cell_w: int,
    cell_h: int = 0,
    margin_l: int = 141,
    margin_r: int = 141,
    margin_t: int = 141,
    margin_b: int = 141,
    base_pt100: int = 1000,
    base_spacing: int = 0,
    line_space_pct: int = 160,
    allow_wrap: bool = True,
    base_ratio: int = 100,
) -> tuple[bool, int, int, bool]:
    """셀 안쪽 폭(HWPUNIT)에 텍스트를 맞춘다. 최소 8pt·자간 -10%(90%)까지 허용하며 불가하면 False 반환."""
    if cell_w <= 0 or not text:
        return True, base_pt100, base_spacing, False
    inner_w = max(0, int(cell_w) - max(0, int(margin_l)) - max(0, int(margin_r)))
    inner_h = max(0, int(cell_h) - max(0, int(margin_t)) - max(0, int(margin_b)))
    if inner_w <= 0:
        return False, base_pt100, base_spacing, False

    h0 = max(int(base_pt100 or 1000), 700)
    sp0 = int(base_spacing or 0)
    ratio_eff = max(90, int(base_ratio or 100)) / 100.0

    # 1) 현재 스타일로 한 줄 수용 여부
    if _est_cell_text_width(text, h0, sp0, ratio_eff) <= inner_w:
        return True, h0, sp0, False

    # 2) 글자 크기를 1pt씩 최소 8pt(800)까지, 필요 시 자간 -10%(90%)까지 줄여 한 줄 맞춤
    heights = [h0]
    for h_step in range(((h0 - 1) // 100) * 100, 799, -100):
        if h_step < h0:
            heights.append(h_step)
    if 800 not in heights and h0 >= 800:
        heights.append(800)

    for h_try in heights:
        if h_try < h0 and _est_cell_text_width(text, h_try, sp0, ratio_eff) <= inner_w:
            return True, h_try, sp0, True

    min_h = min(heights)
    for sp_try in (-5, -10):
        if sp_try < sp0 and sp_try >= -10:
            if _est_cell_text_width(text, min_h, sp_try, ratio_eff) <= inner_w:
                return True, min_h, sp_try, True

    # 3) 공백이 포함된 값(주소 등)이고 셀 높이에 여러 줄 여유가 있어 행이 늘어나지 않을 때만 줄바꿈 허용
    if allow_wrap and " " in text.strip() and inner_h > 0:
        ls_eff = max(1.25, max(120, int(line_space_pct or 160)) / 100.0)
        sp_cands = [sp0] + [sp for sp in (-5, -10) if sp < sp0 and sp >= -10]
        for h_try in heights:
            for sp_try in sp_cands:
                n_lines = _wrapped_line_count(text, inner_w, h_try, sp_try, ratio_eff)
                if n_lines * h_try * ls_eff <= inner_h * 0.92:
                    return True, h_try, sp_try, (h_try != h0 or sp_try != sp0)

    return False, h0, sp0, False


def default_profile(today: date | None = None) -> dict[str, str]:
    d = today or date.today()
    return {
        "applicant.name": "홍길동",
        "applicant.birth_date": "1950-01-01",
        "applicant.rrn": "500101-1234567",
        "applicant.phone": "010-0000-0000",
        "applicant.address": "서울특별시 구로구 가마산로 000",
        "account.bank": "농협",
        "account.number": "000-0000-0000-00",
        "account.holder": "홍길동",
        "account.full": "농협 000-0000-0000-00",
        "applicant.income_monthly": "1,000,000",
        "proxy.name": "김대리",
        "proxy.relation": "자녀",
        "proxy.phone": "010-1111-1111",
        "apply_date": d.strftime("%Y년 %m월 %d일"),
        "apply_date_dot": d.strftime("%Y. %m. %d."),
    }


def _fill_or_skip(
    prof: dict[str, str],
    field_key: str,
    reason: str,
    section: str,
) -> MatchDecision:
    val = (prof.get(field_key) or "").strip()
    if not val:
        return MatchDecision("skip", field_key=field_key, reason="missing_profile_value", section=section)
    return MatchDecision("fill", field_key=field_key, value=val, reason=reason, section=section)


def match_label(
    raw_label: str,
    section: str = "applicant",
    profile: dict[str, str] | None = None,
) -> MatchDecision:
    """라벨 텍스트와 현재 구역(applicant/proxy/excluded)을 기반으로 매칭 결정을 내린다."""
    prof = default_profile() if profile is None else profile
    label_clean = re.sub(r"\s+", " ", (raw_label or "")).strip()
    norm = normalize_label(label_clean)

    if not norm or len(norm) > 40:
        return MatchDecision("skip", reason="empty_or_too_long_label", section=section)

    # 1. 서명·날인 우선 차단
    if SENSITIVE_RE.search(label_clean) or SENSITIVE_RE.search(norm):
        return MatchDecision("sensitive_blank", reason="signature_must_remain_blank", section=section)

    # 2. 제외 / 다른 사람 구역 차단
    if section == "excluded":
        return MatchDecision("skip", reason="excluded_section", section=section)
    if section == "other_person":
        return MatchDecision("skip", reason="other_person", section=section)

    is_relation_lbl = (
        norm in (
            "관계", "신청인과의관계", "신청인과관계", "대상자와의관계", "지원대상자와의관계",
            "지급대상자와의관계", "수급권자와의관계", "출산자와의관계", "본인과의관계",
            "장애인과의관계", "보훈대상자와의관계", "임산부와관계", "임산부와의관계",
        )
        or norm.endswith(("와의관계", "와관계"))
    )

    # 3. 오탐 라벨 차단 (공백·줄바꿈 제거된 norm 기준)
    if NON_TARGET_LABEL_RE.search(norm):
        return MatchDecision("skip", reason="non_target_label", section=section)

    # 라벨 자체에 사망인·타인 유형('출산자성명', '임산부성명' 등)이 들어 있으면 건너뜀 (관계 라벨 제외)
    if not is_relation_lbl:
        if DECEASED_SECTION_RE.search(norm):
            return MatchDecision("skip", reason="other_person", section="other_person")
        if (
            OTHER_PERSON_SECTION_RE.search(norm)
            and not APPLICANT_SECTION_RE.search(norm)
        ):
            return MatchDecision("skip", reason="other_person", section="other_person")

    eff_section = section
    if DECEASED_SECTION_RE.search(norm):
        return MatchDecision("skip", reason="other_person", section="other_person")
    if APPLICANT_MIXED_RE.search(label_clean):
        eff_section = "applicant"
    elif PROXY_SECTION_RE.search(label_clean):
        eff_section = "proxy"
    elif re.search(r"^(신청인|신청자|신고인|청구인|본인|위임인|수급자|이용자)", norm):
        eff_section = "applicant"

    # 4. 주민등록번호 (서식 라벨에 외국인등록번호 전산관리번호 등이 괄호로 붙는 경우 포함)
    if (
        norm.startswith(("주민등록번호", "주민번호"))
        or norm in ("주민등록번호외국인등록번호전산관리번호", "신청인주민등록번호")
    ):
        if eff_section == "proxy":
            return MatchDecision("skip", reason="no_proxy_rrn_in_profile", section=eff_section)
        return _fill_or_skip(prof, "applicant.rrn", "matched_rrn", eff_section)

    # 5. 계좌 관련 항목 (예금주 / 은행·금융기관 / 계좌번호 / 통합 계좌) — 대리인 구역은 계좌 비움
    is_holder_lbl = norm in ("예금주", "예금주명", "예금주성명") or norm.startswith("예금주신청인")
    is_bank_lbl = norm in ("은행명", "금융기관", "금융기관명", "거래은행", "입금은행", "은행", "은행기관명")
    is_acct_num_lbl = norm in ("계좌번호", "입금계좌번호", "수령계좌번호", "계좌", "신청인명의의계좌번호")
    is_acct_full_lbl = norm in ("예금계좌", "지급계좌", "수령계좌", "입금계좌", "계좌정보")
    if is_holder_lbl or is_bank_lbl or is_acct_num_lbl or is_acct_full_lbl:
        if eff_section == "proxy":
            return MatchDecision("skip", reason="no_proxy_account_in_profile", section=eff_section)
        if is_holder_lbl:
            return _fill_or_skip(prof, "account.holder", "matched_account_holder", eff_section)
        if is_bank_lbl:
            return _fill_or_skip(prof, "account.bank", "matched_account_bank", eff_section)
        if is_acct_num_lbl:
            return _fill_or_skip(prof, "account.number", "matched_account_number", eff_section)
        return _fill_or_skip(prof, "account.full", "matched_account_full", eff_section)

    # 6. 월 소득 (연간소득·가구원·배우자 소득 및 대리인 구역 차단)
    if norm in ("월평균소득", "월소득", "월소득액", "월평균소득액"):
        if eff_section == "proxy":
            return MatchDecision("skip", reason="no_proxy_income_in_profile", section=eff_section)
        return _fill_or_skip(prof, "applicant.income_monthly", "matched_monthly_income", eff_section)

    # 7. 성명
    if norm in (
        "성명", "성명한글", "한글성명", "이름", "성명예금주",
        "신청인성명", "수급자성명", "이용자성명", "가입자성명",
        "대리인성명", "위임인성명", "수임인성명", "신청자성명",
        "위임하는사람성명", "위임받는사람성명",
    ) or re.fullmatch(r"(신청인|대리인|위임인|수임인|본인|신청자)?성명", norm):
        key = "proxy.name" if eff_section == "proxy" else "applicant.name"
        return _fill_or_skip(prof, key, "matched_name", eff_section)

    # 8. 생년월일
    if norm in ("생년월일", "출생연월일", "출생년월일", "신청인생년월일"):
        if eff_section == "proxy":
            return MatchDecision("skip", reason="no_proxy_birth_date_in_profile", section=eff_section)
        return _fill_or_skip(prof, "applicant.birth_date", "matched_birth_date", eff_section)

    # 9. 관계 (대리인 구역에서만 기입, 신청인 구역에서는 어떤 관계 칸도 채우지 않음)
    if is_relation_lbl:
        if eff_section == "proxy":
            return _fill_or_skip(prof, "proxy.relation", "matched_relation", "proxy")
        return MatchDecision("skip", reason="ambiguous_relation_section", section=eff_section)

    # 10. 휴대전화 / 연락처 / 전화번호 / HP
    if any(k in norm for k in ("휴대전화", "휴대폰", "핸드폰", "연락처", "전화번호", "전화")) or norm.upper() in ("HP", "CP", "MOBILE"):
        if any(bad in norm for bad in ("팩스", "비상", "직장", "회사", "담당", "기관")):
            return MatchDecision("skip", reason="non_applicant_phone", section=eff_section)
        if "자택" in norm or "집전화" in norm:
            return MatchDecision("skip", reason="home_phone_not_mobile", section=eff_section)
        key = "proxy.phone" if eff_section == "proxy" else "applicant.phone"
        return _fill_or_skip(prof, key, "matched_phone", eff_section)

    # 11. 주소 / 거주지
    if norm in ("주소", "현주소", "거주지", "주민등록상주소", "주민등록주소", "주민등록지", "신청인주소"):
        if eff_section == "proxy":
            return MatchDecision("skip", reason="no_proxy_address_in_profile", section=eff_section)
        return _fill_or_skip(prof, "applicant.address", "matched_address", eff_section)

    # 12. 신청일 / 작성일
    if norm in ("신청일", "신청일자", "작성일", "작성일자", "제출일"):
        return _fill_or_skip(prof, "apply_date_dot", "matched_apply_date", eff_section)

    return MatchDecision("skip", reason="unmatched_label", section=eff_section)
