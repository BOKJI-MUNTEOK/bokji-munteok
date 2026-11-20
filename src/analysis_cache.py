"""저장된 추출 결과를 재사용하고, 바뀐 원문만 다시 분석한다."""
import hashlib
import inspect
import json
from collections import OrderedDict
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from threading import RLock

import rules
import score


# 추출 규칙과 난이도 계산에 실제로 쓰는 필드만 비교한다. 확인 시각·정렬·태그는 제외한다.
FEATURE_FIELDS = ("src", "online_flag", "apply_method_name", "apply", "agencies", "target", "criteria")
_engine = (Path(rules.__file__).read_text(encoding="utf-8") + "\n"
           + inspect.getsource(score.readability) + "\n" + json.dumps(score.HARD_WORDS, ensure_ascii=False))
FEATURE_VERSION = hashlib.sha256(_engine.replace("\r\n", "\n").encode()).hexdigest()
# 버전을 기록하기 전 MongoDB에 저장한 결과는 이 추출 규칙으로 만들었다.
# 규칙이 바뀌면 무버전 결과를 그대로 쓰지 않고 해당 원문을 한 번 다시 분석한다.
LEGACY_FEATURE_VERSION = "95e8b7635c830139710ec5b82a2671aead07b19ef985ea2554857704724c54b7"
_features = OrderedDict()
_lock = RLock()
MAX_FEATURES = 8192


def analysis_hash(service: dict) -> str:
    text = json.dumps({k: service.get(k) for k in FEATURE_FIELDS}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


def _valid_features(ext, read):
    if not isinstance(ext, dict) or not isinstance(read, dict):
        return False
    if not all(isinstance(ext.get(k), bool) for k in ("online", "visit_required")):
        return False
    if not isinstance(ext.get("agencies"), list) or not all(isinstance(x, str) for x in ext["agencies"]):
        return False
    if not isinstance(ext.get("conditions"), list) or not all(
            isinstance(x, dict) and isinstance(x.get("type"), str) and isinstance(x.get("text"), str)
            for x in ext["conditions"]):
        return False
    docs = ext.get("documents")
    if "documents" not in ext or (docs is not None and
                                  (not isinstance(docs, list) or not all(isinstance(x, str) for x in docs))):
        return False
    return (type(read.get("points")) is int and 0 <= read["points"] <= 3
            and isinstance(read.get("avg_len"), (int, float))
            and isinstance(read.get("hard_words"), list)
            and all(isinstance(x, str) for x in read["hard_words"]))


def _stored_features(service, fingerprint):
    stored = service.get("stored_analysis")
    if not isinstance(stored, dict):
        return None
    version = stored.get("feature_version")
    if version is None:
        if FEATURE_VERSION != LEGACY_FEATURE_VERSION:
            return None
    elif version != FEATURE_VERSION or stored.get("source_hash") != fingerprint:
        return None
    if not _valid_features(stored.get("ext"), stored.get("read")):
        return None
    return stored["ext"], stored["read"]


def features(service: dict, rule_required=True) -> tuple[dict | None, dict]:
    """DB의 같은 규칙 결과를 먼저 쓰고, 구형·미저장 원문만 한 번 분석한다."""
    fingerprint = analysis_hash(service)
    key = (FEATURE_VERSION, fingerprint)
    with _lock:
        if key in _features:
            _features.move_to_end(key)
            if rule_required and _features[key][0] is None:
                _features[key] = rules.extract(service), _features[key][1]
            return deepcopy(_features[key])
        value = _stored_features(service, fingerprint)
        if value is None:
            value = rules.extract(service) if rule_required else None, score.readability(service)
        _features[key] = deepcopy(value)
        if len(_features) > MAX_FEATURES:
            _features.popitem(last=False)
        return deepcopy(value)


@lru_cache(maxsize=16384)
def _weighted_score(features_json, weights):
    ext, read = json.loads(features_json)
    return score.score(ext, read, dict(weights))


def weighted_score(ext: dict, read: dict, weights: dict | None = None) -> dict:
    """원문 추출 없이 가중합만 계산하며 같은 추출값·가중치의 결과를 재사용한다."""
    values = tuple(sorted({**score.DEFAULT_WEIGHTS, **(weights or {})}.items()))
    data = json.dumps((ext, read), ensure_ascii=False, sort_keys=True)
    return deepcopy(_weighted_score(data, values))


def clear_cache():
    """검증·명시적 캐시 초기화용. 원본이나 DB 데이터는 변경하지 않는다."""
    with _lock:
        _features.clear()
        _weighted_score.cache_clear()
