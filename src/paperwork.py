"""서류 준비 도움: 서류 목록, 발급처, 방문이 어려울 때 쓸 말. 개인정보 없이 데이터만으로 만든다."""
import re

# 안내문에 서류 목록이 없을 때 먼저 보여 줄 기본 서류
BASIC = ["신청서 (기관 양식)", "신분증", "본인 명의 통장사본"]

# (서류 이름 패턴, 어디서 떼나요, 온라인 가능)
WHERE = [
    (r"신청서|동의서|서약서|위임장", "첨부 양식을 내려받거나 주민센터·담당 기관에 비치된 양식", False),
    (r"신분증", "주민등록증·운전면허증·여권 중 하나", False),
    (r"통장", "본인 은행 앱(통장사본 출력) 또는 은행 지점", True),
    (r"등본|초본|주민등록", "정부24, 주민센터, 무인민원발급기", True),
    (r"가족관계|기본증명", "전자가족관계등록시스템, 정부24, 주민센터", True),
    (r"소득금액|사실증명|원천징수|납세|소득", "홈택스(온라인), 세무서", True),
    (r"진단서|소견서|처방전|진료", "진료받은 병원", False),
    (r"영수증|입금", "결제한 곳 또는 은행 앱 거래내역", True),
    (r"대출|이자", "대출받은 은행 앱 또는 지점", True),
    (r"임대차|계약서", "집 계약 때 받은 계약서 사본", False),
    (r"장애인증명|장애", "정부24, 주민센터", True),
]


def where(doc: str) -> tuple[str, bool] | None:
    return next(((w, on) for p, w, on in WHERE if re.search(p, doc)), None)


def documents(row: dict) -> tuple[list[str], bool]:
    """(서류 목록, 안내문에 있던 목록인지). AI가 뽑은 목록 → 규칙 목록 → 기본 서류 순."""
    docs = ((row.get("ai") or {}).get("extract") or {}).get("documents") or row["ext"].get("documents")
    return (docs, True) if docs else (list(BASIC), False)


def call_script(svc: dict) -> str:
    return (f"안녕하세요. '{svc['servNm']}'을(를) 신청하고 싶은데, 몸이 불편해서 직접 방문하기 어렵습니다.\n"
            "방문 상담이나 직권 신청(담당자가 대신 신청을 진행)이 가능한지 여쭤봅니다.\n"
            "필요한 서류와 가족이 대신 낼 수 있는지도 알려 주세요.")
