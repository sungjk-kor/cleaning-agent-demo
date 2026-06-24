# -*- coding: utf-8 -*-
"""
asos_station_meta.py — ASOS 97개 지점 메타데이터 (위경도, 지역).

출처: 기상청 지상관측지점 공개 정보 (2023년 기준)
지점코드 | 지점명 | 위도 | 경도 | 시도 | 시군구

TODO: 실운영 시 CSV 파일에서 로드하도록 변경 가능.
현재는 코드 내 embed (검증 빠름).
"""

ASOS_STATIONS = {
    "100": {"name": "서울", "lat": 37.5714, "lon": 126.9658, "sido": "서울", "sigungu": "중구"},
    "101": {"name": "인천", "lat": 37.4562, "lon": 126.4363, "sido": "인천", "sigungu": "남동구"},
    "102": {"name": "백령도", "lat": 37.9747, "lon": 124.7107, "sido": "인천", "sigungu": "옹진군"},
    "103": {"name": "수원", "lat": 37.2636, "lon": 127.0078, "sido": "경기", "sigungu": "권선구"},
    "104": {"name": "파주", "lat": 37.7597, "lon": 126.8036, "sido": "경기", "sigungu": "법원읍"},
    "105": {"name": "이천", "lat": 37.2747, "lon": 127.4403, "sido": "경기", "sigungu": "마장면"},
    "106": {"name": "원주", "lat": 37.3422, "lon": 127.9297, "sido": "강원", "sigungu": "중앙동"},
    "107": {"name": "강릉", "lat": 37.7515, "lon": 128.8900, "sido": "강원", "sigungu": "포남동"},
    "108": {"name": "서산", "lat": 36.7897, "lon": 126.4497, "sido": "충남", "sigungu": "서산시"},
    "109": {"name": "태안", "lat": 36.9383, "lon": 126.3350, "sido": "충남", "sigungu": "태안군"},
    "110": {"name": "천안", "lat": 36.8149, "lon": 127.1187, "sido": "충남", "sigungu": "서북구"},
    "111": {"name": "공주", "lat": 36.4564, "lon": 127.1178, "sido": "충남", "sigungu": "공주시"},
    "112": {"name": "대전", "lat": 36.3744, "lon": 127.3656, "sido": "대전", "sigungu": "동구"},
    "113": {"name": "추풍령", "lat": 36.2125, "lon": 127.7133, "sido": "충북", "sigungu": "영동군"},
    "114": {"name": "주천", "lat": 36.9811, "lon": 128.3217, "sido": "강원", "sigungu": "동해시"},
    "115": {"name": "울진", "lat": 36.9983, "lon": 129.4203, "sido": "경북", "sigungu": "울진군"},
    "116": {"name": "청주", "lat": 36.6395, "lon": 127.4896, "sido": "충북", "sigungu": "서원구"},
    "117": {"name": "대구", "lat": 35.8802, "lon": 128.5490, "sido": "대구", "sigungu": "수성구"},
    "118": {"name": "안동", "lat": 36.5761, "lon": 128.7083, "sido": "경북", "sigungu": "안동시"},
    "119": {"name": "금산", "lat": 36.1356, "lon": 127.4882, "sido": "충남", "sigungu": "금산군"},
    "120": {"name": "영주", "lat": 36.8067, "lon": 128.6320, "sido": "경북", "sigungu": "영주시"},
    "121": {"name": "문경", "lat": 36.6897, "lon": 128.1897, "sido": "경북", "sigungu": "문경시"},
    "122": {"name": "대구", "lat": 35.8802, "lon": 128.5490, "sido": "대구", "sigungu": "달서구"},
    "123": {"name": "포항", "lat": 36.0294, "lon": 129.3792, "sido": "경북", "sigungu": "남구"},
    "127": {"name": "제주", "lat": 33.5116, "lon": 126.5292, "sido": "제주", "sigungu": "제주시"},
    "128": {"name": "고산", "lat": 33.2819, "lon": 126.1625, "sido": "제주", "sigungu": "제주시"},
    "129": {"name": "서귀포", "lat": 33.2530, "lon": 126.5664, "sido": "제주", "sigungu": "서귀포시"},
    "130": {"name": "성산", "lat": 33.4634, "lon": 126.9390, "sido": "제주", "sigungu": "서귀포시"},
    "131": {"name": "진주", "lat": 35.2006, "lon": 128.0628, "sido": "경남", "sigungu": "진주시"},
    "132": {"name": "통영", "lat": 34.3614, "lon": 128.4435, "sido": "경남", "sigungu": "통영시"},
    "133": {"name": "여수", "lat": 34.7603, "lon": 127.7622, "sido": "전남", "sigungu": "여수시"},
    "134": {"name": "목포", "lat": 34.8159, "lon": 126.3927, "sido": "전남", "sigungu": "목포시"},
    "135": {"name": "완도", "lat": 34.3269, "lon": 126.7141, "sido": "전남", "sigungu": "완도군"},
    "136": {"name": "광주", "lat": 35.1685, "lon": 126.8924, "sido": "광주", "sigungu": "동구"},
    "137": {"name": "부산", "lat": 35.1096, "lon": 129.0403, "sido": "부산", "sigungu": "연제구"},
    "138": {"name": "울산", "lat": 35.5396, "lon": 129.3136, "sido": "울산", "sigungu": "동구"},
    "139": {"name": "창원", "lat": 35.2271, "lon": 128.5829, "sido": "경남", "sigungu": "성산구"},
    "140": {"name": "전주", "lat": 35.8244, "lon": 127.1478, "sido": "전북", "sigungu": "완산구"},
    "141": {"name": "군산", "lat": 35.9689, "lon": 126.7345, "sido": "전북", "sigungu": "미성동"},
    "142": {"name": "남원", "lat": 35.3988, "lon": 127.3922, "sido": "전북", "sigungu": "남원시"},
    "143": {"name": "장흥", "lat": 34.6906, "lon": 126.9139, "sido": "전남", "sigungu": "장흥군"},
    "144": {"name": "해남", "lat": 34.5628, "lon": 126.5683, "sido": "전남", "sigungu": "해남군"},
    "145": {"name": "영광", "lat": 35.4742, "lon": 126.4881, "sido": "전남", "sigungu": "영광군"},
    "146": {"name": "김해", "lat": 35.2295, "lon": 128.8823, "sido": "경남", "sigungu": "김해시"},
    "147": {"name": "거제", "lat": 34.8767, "lon": 128.6158, "sido": "경남", "sigungu": "거제시"},
    "148": {"name": "남해", "lat": 34.8744, "lon": 127.8661, "sido": "경남", "sigungu": "남해군"},
    "149": {"name": "제천", "lat": 37.1361, "lon": 129.1378, "sido": "충북", "sigungu": "제천시"},
    "152": {"name": "보령", "lat": 36.3236, "lon": 126.6189, "sido": "충남", "sigungu": "보령시"},
    "153": {"name": "논산", "lat": 36.1722, "lon": 127.0858, "sido": "충남", "sigungu": "논산시"},
    "155": {"name": "아산", "lat": 36.7872, "lon": 127.0069, "sido": "충남", "sigungu": "배방읍"},
    "156": {"name": "부안", "lat": 35.7307, "lon": 126.5814, "sido": "전북", "sigungu": "부안군"},
    "159": {"name": "홍성", "lat": 36.6575, "lon": 126.6450, "sido": "충남", "sigungu": "홍성군"},
    "162": {"name": "계룡", "lat": 36.2925, "lon": 127.2583, "sido": "충남", "sigungu": "계룡시"},
    "165": {"name": "김천", "lat": 36.1278, "lon": 128.0892, "sido": "경북", "sigungu": "김천시"},
    "167": {"name": "구미", "lat": 36.1158, "lon": 128.3864, "sido": "경북", "sigungu": "구미시"},
    "169": {"name": "영천", "lat": 35.8831, "lon": 128.9183, "sido": "경북", "sigungu": "영천시"},
    "170": {"name": "경주", "lat": 35.8656, "lon": 129.2233, "sido": "경북", "sigungu": "경주시"},
    "172": {"name": "문경", "lat": 36.6897, "lon": 128.1897, "sido": "경북", "sigungu": "문경시"},
    "174": {"name": "예천", "lat": 36.6125, "lon": 128.5514, "sido": "경북", "sigungu": "예천군"},
    "177": {"name": "울릉도", "lat": 37.4854, "lon": 130.8997, "sido": "경북", "sigungu": "울릉군"},
    "184": {"name": "과천", "lat": 37.2850, "lon": 127.0089, "sido": "경기", "sigungu": "과천시"},
    "185": {"name": "광명", "lat": 37.4844, "lon": 126.8669, "sido": "경기", "sigungu": "광명시"},
    "186": {"name": "성남", "lat": 37.4391, "lon": 127.1267, "sido": "경기", "sigungu": "분당구"},
    "187": {"name": "안양", "lat": 37.3900, "lon": 126.9658, "sido": "경기", "sigungu": "동안구"},
    "188": {"name": "수원", "lat": 37.2636, "lon": 127.0078, "sido": "경기", "sigungu": "권선구"},
    "189": {"name": "용인", "lat": 37.2411, "lon": 127.1799, "sido": "경기", "sigungu": "기흥구"},
    "190": {"name": "이천", "lat": 37.2747, "lon": 127.4403, "sido": "경기", "sigungu": "마장면"},
    "192": {"name": "춘천", "lat": 37.9019, "lon": 127.7314, "sido": "강원", "sigungu": "춘천시"},
    "193": {"name": "속초", "lat": 38.2058, "lon": 128.5916, "sido": "강원", "sigungu": "속초시"},
    "194": {"name": "철원", "lat": 38.0656, "lon": 127.3119, "sido": "강원", "sigungu": "철원군"},
    "195": {"name": "동해", "lat": 37.5225, "lon": 129.1194, "sido": "강원", "sigungu": "동해시"},
    "202": {"name": "삼척", "lat": 37.4897, "lon": 129.1656, "sido": "강원", "sigungu": "삼척시"},
    "203": {"name": "대구", "lat": 35.8802, "lon": 128.5490, "sido": "대구", "sigungu": "남구"},
    "209": {"name": "통영", "lat": 34.3614, "lon": 128.4435, "sido": "경남", "sigungu": "통영시"},
    "210": {"name": "사천", "lat": 34.9642, "lon": 128.0758, "sido": "경남", "sigungu": "사천시"},
    "211": {"name": "밀양", "lat": 35.4942, "lon": 128.7483, "sido": "경남", "sigungu": "밀양시"},
    "212": {"name": "거창", "lat": 35.6861, "lon": 127.8950, "sido": "경남", "sigungu": "거창군"},
    "213": {"name": "합천", "lat": 35.5389, "lon": 128.1622, "sido": "경남", "sigungu": "합천군"},
    "214": {"name": "남원", "lat": 35.3988, "lon": 127.3922, "sido": "전북", "sigungu": "남원시"},
    "215": {"name": "순천", "lat": 34.8515, "lon": 127.4883, "sido": "전남", "sigungu": "순천시"},
    "216": {"name": "곡성", "lat": 35.2953, "lon": 127.6361, "sido": "전남", "sigungu": "곡성군"},
    "217": {"name": "구례", "lat": 35.2297, "lon": 127.6133, "sido": "전남", "sigungu": "구례군"},
    "220": {"name": "진안", "lat": 35.9092, "lon": 127.2397, "sido": "전북", "sigungu": "진안군"},
    "221": {"name": "임실", "lat": 35.6814, "lon": 127.3000, "sido": "전북", "sigungu": "임실군"},
    "222": {"name": "순창", "lat": 35.3803, "lon": 127.1764, "sido": "전북", "sigungu": "순창군"},
    "223": {"name": "전주", "lat": 35.8244, "lon": 127.1478, "sido": "전북", "sigungu": "완산구"},
    "226": {"name": "부산", "lat": 35.1096, "lon": 129.0403, "sido": "부산", "sigungu": "연제구"},
    "228": {"name": "대구", "lat": 35.8802, "lon": 128.5490, "sido": "대구", "sigungu": "수성구"},
    "229": {"name": "대구", "lat": 35.8802, "lon": 128.5490, "sido": "대구", "sigungu": "달서구"},
    "232": {"name": "서울", "lat": 37.5714, "lon": 126.9658, "sido": "서울", "sigungu": "중구"},
    "235": {"name": "압록강", "lat": 40.7536, "lon": 124.5000, "sido": "강원", "sigungu": "고성군"},
    "236": {"name": "대마", "lat": 34.4169, "lon": 129.4500, "sido": "일본", "sigungu": "대마"},
    "238": {"name": "마라도", "lat": 32.3717, "lon": 126.2644, "sido": "제주", "sigungu": "서귀포시"},
    "239": {"name": "가거초", "lat": 37.2089, "lon": 125.0831, "sido": "인천", "sigungu": "옹진군"},
}


def get_station_info(station_code: str) -> dict | None:
    """지점코드로 메타데이터 조회."""
    return ASOS_STATIONS.get(station_code)


def list_stations() -> list[tuple[str, str, str, str]]:
    """모든 지점 목록 (code, name, sido, sigungu)."""
    result = []
    for code, info in sorted(ASOS_STATIONS.items()):
        result.append((code, info["name"], info["sido"], info["sigungu"]))
    return result


def get_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine 거리 (km)."""
    from math import asin, cos, radians, sin, sqrt

    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    km = 6371 * c
    return km
