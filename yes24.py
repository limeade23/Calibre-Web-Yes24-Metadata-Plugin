# -*- coding: utf-8 -*-

import concurrent.futures
import re
import threading
from datetime import datetime
from typing import List, Optional

import requests
import lxml.html  # calibre-web 이 이미 의존한다

from cps.services.Metadata import MetaRecord, MetaSourceInfo, Metadata
import cps.logger as logger

log = logger.create()

_HEADERS = {
    'user-agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0',
    'accept-language': 'ko-KR,ko;q=0.9,en;q=0.8',
}

_thread_local = threading.local()


def _session() -> requests.Session:
    """스레드마다 별도 Session 을 사용한다.

    하나의 Session 을 여러 스레드가 공유하면 커넥션 풀 경합 때문에
    순차 요청보다도 느려진다.
    """
    session = getattr(_thread_local, 'session', None)
    if session is None:
        session = requests.Session()
        session.headers.update(_HEADERS)
        _thread_local.session = session
    return session


# --- lxml 헬퍼 ---------------------------------------------------------------

def _cls(tag: str, name: str) -> str:
    """class 에 name 이 들어있는 요소를 찾는 XPath 를 만든다.

    class="gd_name foo" 에서 gd_name 만 정확히 고르려면 앞뒤에 공백을 붙여
    비교해야 한다. contains(@class, "gd_name") 만으로는 "gd_name_sub" 같은
    다른 클래스에도 걸린다.
    cssselect 를 쓰면 간단하지만 calibre-web 의 의존성이 아니라 XPath 만 쓴다.
    """
    return f'.//{tag}[contains(concat(" ", normalize-space(@class), " "), " {name} ")]'


def _first(node, xpath):
    found = node.xpath(xpath)
    return found[0] if found else None


def _text(node, xpath) -> str:
    element = _first(node, xpath)
    return element.text_content().strip() if element is not None else ''


def _inner_html(element) -> str:
    """자식의 마크업을 유지한 채 내부 HTML 을 문자열로 만든다."""
    children = ''.join(
        lxml.html.tostring(child, encoding='unicode') for child in element)
    return ((element.text or '') + children).strip()


_CLOSE_BR = re.compile(r'(&lt;|<)\s*/\s*br\s*/?\s*(&gt;|>)', re.IGNORECASE)


def _fix_close_br(html: str) -> str:
    """void 요소를 잘못 닫은 </br> 을 <br> 로 바꾼다.

    HTML 스펙상 브라우저는 </br> 을 <br> 로 취급하지만 lxml 의 libxml2 파서는
    그냥 버린다. Yes24 설명에 이 표기가 흔해서 그대로 두면 줄바꿈이 사라진다.
    설명은 상품에 따라 이스케이프된 채로도, 날 것으로도 들어있어 둘 다 처리한다.
    """
    def to_open_tag(match):
        was_escaped = match.group(1) == '&lt;'
        return '&lt;br&gt;' if was_escaped else '<br>'

    return _CLOSE_BR.sub(to_open_tag, html)


# --- 저자 -------------------------------------------------------------------

# 정규식 교대는 왼쪽부터 먼저 맞춰보므로, 어떤 표기가 다른 표기의 앞부분이면
# 반드시 긴 쪽을 왼쪽에 둬야 한다. '공저' 가 '저' 보다 뒤에 있으면 '저' 로 잘린다.
# 표기를 추가할 때도 이 규칙을 지켜야 한다 ('역주' 는 '역' 보다 앞에).
_ROLE = (r'(?:원저|편저|공저|등저|저자|지음|저|글|그림|사진|편역|번역|옮김|역자|역'
         r'|엮음|편|감수|해설|주해|각색|기획|원작|구성)')
_ROLE_IN_TAIL = re.compile(_ROLE)          # 이름 뒤 tail 텍스트에서 역할 찾기
_ROLE_SUFFIX = re.compile(r'\s+' + _ROLE + r'\s*$')   # 이름 끝에 붙은 역할 떼기

# 글쓴 사람으로 볼 역할. 아무도 빼지 않고 이 사람들을 앞으로 보내기만 한다.
# calibre-web 은 authors 를 ' & ' 로 이어 붙인 뒤 첫 번째 사람으로 책 폴더를
# 만들고 author_sort 를 조립하므로 (editbooks.py 의 prepare_authors,
# handle_author_on_edit), 글쓴 사람이 맨 앞에 와야 한다.
# 나머지(역/그림/감수/기획...)끼리는 Yes24 가 준 순서를 그대로 둔다. 우열을
# 매길 근거가 없어서, 굳이 뒤섞으면 바뀌지 않아도 될 순서까지 바뀐다.
_AUTHOR_ROLES = frozenset((
    '저', '저자', '지음', '글', '공저', '등저', '원저', '편저', '엮음', '원작', '구성'))


def _is_author(role: str) -> bool:
    """글쓴 사람인지. 역할 표기가 없으면 (Yes24 가 생략한 경우) 글쓴 사람으로 본다."""
    return not role or role in _AUTHOR_ROLES


def _clean_author(name: str) -> str:
    """저자명 뒤에 붙어 들어오는 역할 표기를 떼어낸다.

    Yes24 는 링크 텍스트 자체에 역할이 섞인 상품이 있다 ('문현일 저', '진유림 공저').
    """
    return _ROLE_SUFFIX.sub('', name).strip()


def _parse_authors(doc) -> List[str]:
    """글쓴 사람이 앞에 오도록 정렬한다. 역자/감수도 버리지 않는다.

    gd_auth 는 <a>이름</a> 뒤의 tail 텍스트에 역할이 붙는 구조다.
        <a>유발 하라리</a> 저/<a>다니엘 카사나브</a> 그림/<a>김명주</a> 역
    """
    authors_element = _first(doc, _cls('span', 'gd_auth'))
    if authors_element is None:
        return []

    # 저자가 많으면 접힌 목록에 전체가 들어있다. 다만 이 링크들은 tail 이 비어
    # 역할을 알 수 없어 Yes24 가 준 순서를 그대로 쓴다.
    more_auth_li = _first(authors_element, _cls('span', 'moreAuthLi'))
    if more_auth_li is not None:
        return [_clean_author(a.text_content().strip()) for a in more_auth_li.xpath('.//a')]

    # 쉼표로만 이어진 이름은 자기 tail 에 역할이 없다. 역할 표기가 나올 때까지
    # 모아뒀다가 한꺼번에 같은 역할로 확정한다.
    #   <a>차유진</a>, <a>정재승</a> 글/   ->   차유진과 정재승 둘 다 '글'
    people, unassigned = list(), list()
    for anchor in authors_element.xpath('./a'):
        unassigned.append(_clean_author(anchor.text_content().strip()))
        role = _ROLE_IN_TAIL.search(anchor.tail or '')
        if role is None:
            continue
        people += [(name, role.group(0)) for name in unassigned]
        unassigned = list()

    # 끝까지 역할 표기를 못 만난 이름들
    people += [(name, '') for name in unassigned]

    # 안정 정렬이라 글쓴 사람끼리도, 나머지끼리도 원래 순서가 유지된다
    people.sort(key=lambda person: 0 if _is_author(person[1]) else 1)
    return [name for name, _ in people]


# --- 나머지 필드 --------------------------------------------------------------

def _parse_published_date(doc) -> str:
    pub_date = _text(doc, _cls('span', 'gd_date'))
    if not pub_date:
        return ''
    try:
        return datetime.strptime(pub_date, "%Y년 %m월 %d일").strftime("%Y-%m-%d")
    except ValueError:
        return pub_date


def _parse_description(doc) -> str:
    """설명은 display:none 인 textarea 안에 들어있다.

    calibre-web 의 comments 는 TinyMCE 로 편집하는 HTML 이고 저장할 때
    bleach/nh3 로 sanitize 되므로 마크업을 그대로 넘긴다.
    """
    element = _first(doc, _cls('div', 'infoWrap_txtInner'))
    if element is None:
        return ''
    content = _first(element, './/textarea')
    return _inner_html(content if content is not None else element)


def _parse_rating(doc) -> Optional[int]:
    """Yes24 는 10점 만점, calibre-web 은 5점 만점이다."""
    element = _first(doc, _cls('span', 'gd_rating'))
    if element is None:
        return None
    rating_text = _text(element, './/em')
    if not rating_text:
        return None
    try:
        score = float(rating_text)
    except ValueError:
        return None
    # 반올림이 아니라 내림. 9.7 -> 4.85 -> 4
    return max(0, min(5, int(score / 2)))


def _parse_tags(doc) -> List[str]:
    goods_cate = _first(doc, './/div[@id="infoset_goodsCate"]')
    if goods_cate is None:
        return []
    cate_list = _first(goods_cate, _cls('ul', 'yesAlertLi'))
    if cate_list is None:
        return []
    first_item = _first(cate_list, './li')
    if first_item is None:
        return []
    return [a.text_content().strip() for a in first_item.xpath('.//a')]


def _parse_isbn13(doc) -> str:
    return _text(doc, './/th[normalize-space(text())="ISBN13"]/following-sibling::td[1]')


class Yes24(Metadata):
    __name__ = "Yes24"
    __id__ = "Yes24"

    BASE_URL = "https://www.yes24.com/Product"
    DOMAIN = "ALL"
    PAGE_SIZE = 8

    SEARCH_TIMEOUT = 30
    DETAIL_TIMEOUT = 10
    MAX_WORKERS = 8

    def search(
        self, query: str, generic_cover: str = "", locale: str = "ko"
    ) -> Optional[List[MetaRecord]]:
        results = list()

        if self.active:
            try:
                log.debug(f"start searching {query} on yes24")

                response = _session().get(
                    f"{self.BASE_URL}/Search",
                    params={
                        "domain": self.DOMAIN,
                        "query": query,
                        "page": 1,
                        "size": self.PAGE_SIZE,
                    },
                    timeout=self.SEARCH_TIMEOUT,
                )
                response.raise_for_status()

                doc = lxml.html.fromstring(response.text)
                id_list = [str(goods_no)
                           for goods_no in doc.xpath('//li[@data-goods-no]/@data-goods-no')]

                results.extend(self._fetch_all(id_list))

            except requests.RequestException as e:
                log.warning(f"Yes24 search request failed: {e}")
            except Exception as e:
                log.error_or_exception(f"Yes24 search failed: {e}")

        return results

    def _fetch_all(self, id_list: List[str]) -> List[MetaRecord]:
        """상세 페이지를 병렬로 가져온다.

        Yes24 의 관련도 순서를 유지해야 하므로 완료 순서가 아니라 요청 순서대로
        결과를 모은다. 한 건이 실패해도 나머지는 살린다.
        """
        results = list()
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = [executor.submit(self._parse_search_result, goods_no)
                       for goods_no in id_list]
            for goods_no, future in zip(id_list, futures):
                try:
                    result = future.result()
                except Exception as e:
                    log.error_or_exception(f"Unexpected error for goods {goods_no}: {e}")
                    continue
                if result:
                    results.append(result)
        return results

    def _parse_search_result(self, goods_no: str) -> Optional[MetaRecord]:
        try:
            url = f"{self.BASE_URL}/Goods/{goods_no}"

            response = _session().get(url, timeout=self.DETAIL_TIMEOUT)
            response.raise_for_status()

            doc = lxml.html.fromstring(_fix_close_br(response.text))

            identifiers = {"Yes24": goods_no}
            isbn13 = _parse_isbn13(doc)
            if isbn13:
                identifiers["isbn"] = isbn13

            return MetaRecord(
                id=None,
                title=_text(doc, _cls('h2', 'gd_name')),
                authors=_parse_authors(doc),
                source=MetaSourceInfo(
                    id=self.__id__,
                    description="Yes24",
                    link="https://www.yes24.com/"
                ),
                url=url,
                cover=f"https://image.yes24.com/goods/{goods_no}/XL",
                description=_parse_description(doc),
                publisher=_text(doc, _cls('span', 'gd_pub')),
                publishedDate=_parse_published_date(doc),
                rating=_parse_rating(doc),
                tags=_parse_tags(doc),
                identifiers=identifiers,
                languages=["한국어"]
            )

        except requests.RequestException as e:
            log.warning(f"Failed to fetch goods {goods_no} from yes24: {e}")
            return None
        except Exception as e:
            log.error_or_exception(f"Failed to parse goods {goods_no} from yes24: {e}")
            return None
