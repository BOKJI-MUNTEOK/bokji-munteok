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
SENSITIVE_RE = re.compile(r"서명|\(인\)|날인|도장|서명\s*또는\s*인")

# 기입 제외 구역 (가구원, 담당자, 기관, 고인/타인 구역 등)
EXCLUDED_SECTION_RE = re.compile(
    r"가구원|가족\s*사항|동거\s*가족|세대원|세대\s*구성원|부양\s*의무자|동행\s*가족|"
    r"융자\s*대상자|보훈\s*대상자|사망자|고인|배우자\s*성명|배우자\s*생년월일|배우자\s*연간|"
    r"대상자\s*\(\s*아\s*동\s*\)|가입\s*선원|피보험자|권\s*유\s*자|"
    r"미성년자|법정대리인|친권자|후견인|"
    r"담당자|담당\s*공무원|담당\s*직원|처리기관|확인자|접수\s*담당|작성자\s*\(공무원\)|"
    r"추천인|추천기관|보증인|연대보증인|결재|확인란|"
    r"사업장|근무처|직장정보|입소시설|치료기관|의료기관|학교정보"
)

# 대리인 구역
PROXY_SECTION_RE = re.compile(
    r"대리인|위임받는\s*사람|위임\s*받는|수임인|대신\s*신청|대리\s*신청|본인이\s*신청할\s*경우\s*생략"
)

# 신청인(본인) 구역
APPLICANT_SECTION_RE = re.compile(
    r"신청인|수급자|이용자|가입자|청구인|본인|위임하는\s*사람|위임인|"
    r"참여자|신청자|지원\s*신청자|대출신청인|교육생|인적사항|기본사항|지원신청\s*장애인|신청자\s*\(\s*보호자\s*\)"
)

# 라벨 제외 키워드 (성명/이름/주소/연락처/날짜/소득 오탐 방지)
NON_TARGET_LABEL_RE = re.compile(
    r"사업명|(?<!금융)기관명|시설명|단체명|법인명|학교명|병원명|서비스명|계좌명|상품명|질환명|직장명|"
    r"담당\s*부서|문의처|제출처|기관\s*연락처|접수처|대표자\s*성명|사업주|사업장|"
    r"지급일|시행일|접수일|발급일|입금일|결재일|만료일|계약일|출산|사망일|혼인일|전입일|퇴직일|"
    r"입사연월일|성립일|발생일|감소일|개시일|납부일|지정일|"
    r"연간소득|연소득|보수총액|이전월\s*소득|대상월|"
    r"우편번호|이메일|전자우편|팩스|FAX|홈페이지|비고|참고|안내|유의사항"
)


@dataclass
class MatchDecision:
    action: str  # "fill" | "sensitive_blank" | "skip"
    field_key: str | None = None
    value: str | None = None
    reason: str = ""
    section: str = "unknown"


def normalize_label(text: str) -> str:
    s = re.sub(r"[\s\u3000]+", "", text or "")
    s = re.sub(r"[*※①②③④⑤⑥⑦⑧⑨⑩•·:\[\]()（）<>「」『』\-_/]", "", s)
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
    """표/행 문맥에서 현재 구역을 판별한다."""
    if not text:
        return current
    compact = re.sub(r"\s+", " ", text).strip()
    compact_ns = re.sub(r"\s+", "", text)
    # '보훈대상자와의 관계' 같은 관계 라벨 자체는 구역 헤더가 아님 (단, '가족 사항 세대주와의관계'처럼 다른 헤더가 함께 있으면 제외하지 않음)
    if re.fullmatch(r"(?:보훈대상자|지원대상자|신청인|세대주)와의\s*관계", compact) or re.fullmatch(r"(?:보훈대상자|지원대상자|신청인|세대주)와의관계", compact_ns):
        return current
    if EXCLUDED_SECTION_RE.search(compact) or EXCLUDED_SECTION_RE.search(compact_ns):
        return "excluded"
    if PROXY_SECTION_RE.search(compact) or PROXY_SECTION_RE.search(compact_ns):
        return "proxy"
    if APPLICANT_SECTION_RE.search(compact) or APPLICANT_SECTION_RE.search(compact_ns):
        return "applicant"
    return current


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


def match_label(
    raw_label: str,
    section: str = "applicant",
    profile: dict[str, str] | None = None,
) -> MatchDecision:
    """라벨 텍스트와 현재 구역(applicant/proxy/excluded)을 기반으로 매칭 결정을 내린다."""
    prof = profile or default_profile()
    label_clean = re.sub(r"\s+", " ", (raw_label or "")).strip()
    norm = normalize_label(label_clean)

    if not norm or len(norm) > 40:
        return MatchDecision("skip", reason="empty_or_too_long_label", section=section)

    # 1. 서명·날인 우선 차단
    if SENSITIVE_RE.search(label_clean) or SENSITIVE_RE.search(norm):
        return MatchDecision("sensitive_blank", reason="signature_must_remain_blank", section=section)

    # 2. 제외 구역 차단
    if section == "excluded":
        return MatchDecision("skip", reason="excluded_section", section=section)

    # 3. 오탐 라벨 차단
    if NON_TARGET_LABEL_RE.search(label_clean) or NON_TARGET_LABEL_RE.search(norm):
        return MatchDecision("skip", reason="non_target_label", section=section)

    eff_section = section
    if PROXY_SECTION_RE.search(label_clean):
        eff_section = "proxy"
    elif re.search(r"^(신청인|대상자|수급자|이용자|위임인)", norm):
        eff_section = "applicant"

    # 4. 주민등록번호 (서식 라벨에 외국인등록번호 전산관리번호 등이 괄호로 붙는 경우 포함)
    if (
        norm.startswith(("주민등록번호", "주민번호"))
        or norm in ("주민등록번호외국인등록번호전산관리번호", "신청인주민등록번호", "대상자주민등록번호")
    ):
        if eff_section == "proxy":
            return MatchDecision("skip", reason="no_proxy_rrn_in_profile", section=eff_section)
        return MatchDecision(
            "fill",
            field_key="applicant.rrn",
            value=prof["applicant.rrn"],
            reason="matched_rrn",
            section=eff_section,
        )

    # 5. 계좌 관련 항목 (예금주 / 은행·금융기관 / 계좌번호)
    if norm in ("예금주", "예금주명", "예금주성명"):
        return MatchDecision(
            "fill",
            field_key="account.holder",
            value=prof["account.holder"],
            reason="matched_account_holder",
            section=eff_section,
        )
    if norm in ("은행명", "금융기관", "금융기관명", "거래은행", "입금은행", "은행", "은행기관명"):
        return MatchDecision(
            "fill",
            field_key="account.bank",
            value=prof["account.bank"],
            reason="matched_account_bank",
            section=eff_section,
        )
    if norm in ("계좌번호", "입금계좌번호", "수령계좌번호", "계좌", "신청인명의의계좌번호"):
        return MatchDecision(
            "fill",
            field_key="account.number",
            value=prof["account.number"],
            reason="matched_account_number",
            section=eff_section,
        )

    # 6. 월 소득 (연간소득·가구원·배우자 소득은 이미 위에서 차단)
    if norm in ("월평균소득", "월소득", "월소득액", "월평균소득액"):
        return MatchDecision(
            "fill",
            field_key="applicant.income_monthly",
            value=prof["applicant.income_monthly"],
            reason="matched_monthly_income",
            section=eff_section,
        )

    # 7. 성명
    if norm in (
        "성명", "성명한글", "한글성명", "이름",
        "신청인성명", "대상자성명", "수급자성명", "이용자성명", "가입자성명",
        "대리인성명", "위임인성명", "수임인성명", "신청자성명",
        "위임하는사람성명", "위임받는사람성명",
    ) or re.fullmatch(r"(신청인|대상자|대리인|위임인|수임인|본인|신청자)?성명", norm):
        key = "proxy.name" if eff_section == "proxy" else "applicant.name"
        return MatchDecision("fill", field_key=key, value=prof[key], reason="matched_name", section=eff_section)

    # 8. 생년월일
    if norm in ("생년월일", "출생연월일", "출생년월일", "신청인생년월일", "대상자생년월일"):
        if eff_section == "proxy":
            return MatchDecision("skip", reason="no_proxy_birth_date_in_profile", section=eff_section)
        return MatchDecision(
            "fill",
            field_key="applicant.birth_date",
            value=prof["applicant.birth_date"],
            reason="matched_birth_date",
            section=eff_section,
        )

    # 9. 신청인과의 관계 (대리인 구역이거나 명시적 문구일 때만)
    if norm in ("관계", "신청인과의관계", "신청인과관계", "대상자와의관계", "본인과의관계", "장애인과의관계"):
        if eff_section == "proxy" or "신청인" in norm or "대상자" in norm or "본인" in norm or "장애인" in norm:
            return MatchDecision(
                "fill",
                field_key="proxy.relation",
                value=prof["proxy.relation"],
                reason="matched_relation",
                section="proxy",
            )
        return MatchDecision("skip", reason="ambiguous_relation_section", section=eff_section)

    # 10. 휴대전화 / 연락처 / 전화번호
    if any(k in norm for k in ("휴대전화", "휴대폰", "핸드폰", "연락처", "전화번호", "전화")):
        if any(bad in norm for bad in ("팩스", "비상", "직장", "회사", "담당", "기관")):
            return MatchDecision("skip", reason="non_applicant_phone", section=eff_section)
        if "자택" in norm or "집전화" in norm:
            return MatchDecision("skip", reason="home_phone_not_mobile", section=eff_section)
        key = "proxy.phone" if eff_section == "proxy" else "applicant.phone"
        return MatchDecision("fill", field_key=key, value=prof[key], reason="matched_phone", section=eff_section)

    # 11. 주소 / 거주지
    if norm in ("주소", "현주소", "거주지", "주민등록상주소", "주민등록주소", "주민등록지", "신청인주소", "대상자주소"):
        if eff_section == "proxy":
            return MatchDecision("skip", reason="no_proxy_address_in_profile", section=eff_section)
        return MatchDecision(
            "fill",
            field_key="applicant.address",
            value=prof["applicant.address"],
            reason="matched_address",
            section=eff_section,
        )

    # 12. 신청일 / 작성일
    if norm in ("신청일", "신청일자", "작성일", "작성일자", "제출일"):
        return MatchDecision(
            "fill",
            field_key="apply_date",
            value=prof["apply_date_dot"],
            reason="matched_apply_date",
            section=eff_section,
        )

    return MatchDecision("skip", reason="unmatched_label", section=eff_section)
