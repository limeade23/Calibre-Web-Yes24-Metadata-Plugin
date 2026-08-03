# -*- coding: utf-8 -*-

import concurrent.futures
import re
import threading
from datetime import datetime
from typing import List, Optional

import requests
import lxml.html

from cps.services.Metadata import MetaRecord, MetaSourceInfo, Metadata
import cps.logger as logger

log = logger.create()

_HEADERS = {
    'user-agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0',
    'accept-language': 'ko-KR,ko;q=0.9,en;q=0.8',
}

_thread_local = threading.local()


def _session() -> requests.Session:
    session = getattr(_thread_local, 'session', None)
    if session is None:
        session = requests.Session()
        session.headers.update(_HEADERS)
        _thread_local.session = session
    return session


def _cls(tag: str, name: str) -> str:
    return f'.//{tag}[contains(concat(" ", normalize-space(@class), " "), " {name} ")]'


def _first(node, xpath):
    found = node.xpath(xpath)
    return found[0] if found else None


def _text(node, xpath) -> str:
    element = _first(node, xpath)
    return element.text_content().strip() if element is not None else ''


def _inner_html(element) -> str:
    children = ''.join(
        lxml.html.tostring(child, encoding='unicode') for child in element)
    return ((element.text or '') + children).strip()


_CLOSE_BR = re.compile(r'(&lt;|<)\s*/\s*br\s*/?\s*(&gt;|>)', re.IGNORECASE)


def _fix_close_br(html: str) -> str:
    def to_open_tag(match):
        was_escaped = match.group(1) == '&lt;'
        return '&lt;br&gt;' if was_escaped else '<br>'

    return _CLOSE_BR.sub(to_open_tag, html)


_ROLE = (r'(?:원저|편저|공저|등저|저자|지음|저|글|그림|사진|편역|번역|옮김|역자|역'
         r'|엮음|편|감수|해설|주해|각색|기획|원작|구성)')
_ROLE_IN_TAIL = re.compile(_ROLE)
_ROLE_SUFFIX = re.compile(r'\s+' + _ROLE + r'\s*$')

_AUTHOR_ROLES = frozenset((
    '저', '저자', '지음', '글', '공저', '등저', '원저', '편저', '엮음', '원작', '구성'))


def _is_author(role: str) -> bool:
    return not role or role in _AUTHOR_ROLES


def _clean_author(name: str) -> str:
    return _ROLE_SUFFIX.sub('', name).strip()


def _parse_authors(doc) -> List[str]:
    authors_element = _first(doc, _cls('span', 'gd_auth'))
    if authors_element is None:
        return []

    more_auth_li = _first(authors_element, _cls('span', 'moreAuthLi'))
    if more_auth_li is not None:
        return [_clean_author(a.text_content().strip()) for a in more_auth_li.xpath('.//a')]

    people, unassigned = list(), list()
    for anchor in authors_element.xpath('./a'):
        unassigned.append(_clean_author(anchor.text_content().strip()))
        role = _ROLE_IN_TAIL.search(anchor.tail or '')
        if role is None:
            continue
        people += [(name, role.group(0)) for name in unassigned]
        unassigned = list()

    people += [(name, '') for name in unassigned]

    people.sort(key=lambda person: 0 if _is_author(person[1]) else 1)
    return [name for name, _ in people]


def _parse_published_date(doc) -> str:
    pub_date = _text(doc, _cls('span', 'gd_date'))
    if not pub_date:
        return ''
    try:
        return datetime.strptime(pub_date, "%Y년 %m월 %d일").strftime("%Y-%m-%d")
    except ValueError:
        return pub_date


def _parse_description(doc) -> str:
    element = _first(doc, _cls('div', 'infoWrap_txtInner'))
    if element is None:
        return ''
    content = _first(element, './/textarea')
    return _inner_html(content if content is not None else element)


def _parse_rating(doc) -> Optional[int]:
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
    DOMAIN = "BOOK"
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
                    description=self.__name__,
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


class Yes24Ebook(Yes24):
    __name__ = "Yes24-eBook"
    __id__ = "Yes24Ebook"

    DOMAIN = "EBOOK"
