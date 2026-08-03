# -*- coding: utf-8 -*-

import concurrent.futures
import re
import threading
import requests
import lxml.html  # requirement (calibre-web 이 이미 의존)
from datetime import datetime
from typing import List, Optional

from cps import logger
from cps.services.Metadata import MetaRecord, MetaSourceInfo, Metadata
import cps.logger as logger

from operator import itemgetter
log = logger.create()

_thread_local = threading.local()


def _session() -> requests.Session:
    """스레드마다 별도 Session 을 사용한다.

    하나의 Session 을 여러 스레드가 공유하면 커넥션 풀 경합 때문에
    순차 요청보다도 느려진다.
    """
    session = getattr(_thread_local, 'session', None)
    if session is None:
        session = requests.Session()
        _thread_local.session = session
    return session


def _cls(tag: str, name: str) -> str:
    """class 로 요소를 찾는 XPath.

    cssselect 는 calibre-web 의 의존성이 아니므로 XPath 만 사용한다.
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
    return ((element.text or '') + ''.join(
        lxml.html.tostring(child, encoding='unicode') for child in element)).strip()


_CLOSE_BR = re.compile(r'(&lt;|<)\s*/\s*br\s*/?\s*(&gt;|>)', re.IGNORECASE)


def _fix_close_br(html: str) -> str:
    """void 요소를 잘못 닫은 </br> 을 <br> 로 바꾼다.

    HTML 스펙상 브라우저는 </br> 을 <br> 로 취급하지만 lxml 의 libxml2 파서는
    그냥 버린다. Yes24 설명에 이 표기가 흔해서 그대로 두면 줄바꿈이 사라진다.
    설명은 상품에 따라 이스케이프된 채로도, 날 것으로도 들어있어 둘 다 처리한다.
    """
    return _CLOSE_BR.sub(
        lambda m: '&lt;br&gt;' if m.group(1) == '&lt;' else '<br>', html)


class Yes24(Metadata):
    __name__ = "Yes24"
    __id__ = "Yes24"

    BASE_URL = f"https://www.yes24.com/Product"

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

                params = {
                    "domain": "ALL",
                    "query": query,
                    "page": 1,
                    "size": 8
                }

                search_url = f"{self.BASE_URL}/Search"

                response = _session().get(search_url, params=params, timeout=self.SEARCH_TIMEOUT)
                response.raise_for_status()

                doc = lxml.html.fromstring(response.text)

                id_list = [str(goods_no) for goods_no in doc.xpath('//li[@data-goods-no]/@data-goods-no')]

                parsed = list()
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
                    futures = {
                        executor.submit(self._parse_search_result, goods_no): index
                        for index, goods_no in enumerate(id_list)
                    }
                    for future in concurrent.futures.as_completed(futures):
                        index = futures[future]
                        try:
                            result = future.result()
                        except Exception as e:
                            log.error_or_exception(f"Unexpected error for goods {id_list[index]}: {e}")
                            continue
                        if result:
                            parsed.append((index, result))

                # as_completed 는 완료 순서라 Yes24 의 관련도 순서를 되돌린다
                results.extend(result for _, result in sorted(parsed, key=itemgetter(0)))

            except requests.RequestException as e:
                log.warning(f"Yes24 search request failed: {e}")
            except Exception as e:
                log.error_or_exception(f"Yes24 search failed: {e}")

        return results


    def _parse_search_result(self, goods_no) -> Optional[MetaRecord]:
        try:
            url = f"{self.BASE_URL}/Goods/{goods_no}"

            response = _session().get(url, timeout=self.DETAIL_TIMEOUT)
            response.raise_for_status()

            doc = lxml.html.fromstring(_fix_close_br(response.text))

            title = _text(doc, _cls('h2', 'gd_name'))

            authors = []
            authors_element = _first(doc, _cls('span', 'gd_auth'))
            if authors_element is not None:
                more_auth_li = _first(authors_element, _cls('span', 'moreAuthLi'))
                authors = [a.text_content().strip() for a in (more_auth_li.xpath('.//a')
                        if more_auth_li is not None else authors_element.xpath('./a'))]

            publisher = _text(doc, _cls('span', 'gd_pub'))

            pub_date = _text(doc, _cls('span', 'gd_date'))
            try:
                if pub_date:
                    pub_date = datetime.strptime(pub_date, "%Y년 %m월 %d일").strftime("%Y-%m-%d")
            except ValueError:
                pass

            isbn13 = _text(doc, './/th[normalize-space(text())="ISBN13"]/following-sibling::td[1]')

            # 설명은 display:none 인 textarea 안에 HTML 이 이스케이프된 채 들어있다.
            # calibre-web 의 comments 는 TinyMCE 로 편집하는 HTML 이고 저장할 때
            # bleach/nh3 로 sanitize 되므로 마크업을 그대로 넘긴다.
            description = ''
            description_element = _first(doc, _cls('div', 'infoWrap_txtInner'))
            if description_element is not None:
                content_element = _first(description_element, './/textarea')
                description = _inner_html(
                    content_element if content_element is not None else description_element)

            # rating
            rating = None
            rating_element = _first(doc, _cls('span', 'gd_rating'))
            if rating_element is not None:
                rating_text = _text(rating_element, './/em')
                try:
                    if rating_text:
                        rating = float(rating_text)
                        rating = max(0, min(5, round(rating / 2)))
                except ValueError:
                    pass

            # tags
            tags = []
            infoset_goodsCate = _first(doc, './/div[@id="infoset_goodsCate"]')
            if infoset_goodsCate is not None:
                tags_element = _first(infoset_goodsCate, _cls('ul', 'yesAlertLi'))
                if tags_element is not None:
                    first_li = _first(tags_element, './li')
                    if first_li is not None:
                        tags = [a.text_content().strip() for a in first_li.xpath('.//a')]

            identifiers = {"Yes24": goods_no}
            if isbn13:
                identifiers["isbn"] = isbn13

            match = MetaRecord(
                id = None,
                title = title,
                authors = authors,
                source = MetaSourceInfo(
                    id=self.__id__,
                    description="Yes24",
                    link="https://www.Yes24.com/"
                ),
                url = url,
                cover = f"https://image.yes24.com/goods/{goods_no}/XL",
                description = description,
                publisher = publisher,
                publishedDate = pub_date,
                rating = rating,
                tags = tags,
                identifiers = identifiers,
                languages = ["한국어"]
            )

            return match

        except requests.RequestException as e:
            log.warning(f"Failed to fetch goods {goods_no} from yes24: {e}")
            return None
        except Exception as e:
            log.error_or_exception(f"Failed to parse goods {goods_no} from yes24: {e}")
            return None
