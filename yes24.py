# -*- coding: utf-8 -*-

import concurrent.futures
import requests
from datetime import datetime
from bs4 import BeautifulSoup as BS  # requirement
from typing import List, Optional

try:
    import cchardet #optional for better speed
except ImportError:
    pass
from cps import logger
from cps.services.Metadata import MetaRecord, MetaSourceInfo, Metadata
import cps.logger as logger

from operator import itemgetter
log = logger.create()


class Yes24(Metadata):
    __name__ = "Yes24"
    __id__ = "Yes24"
    
    BASE_URL = f"https://www.yes24.com/Product"
    
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

                response = requests.get(search_url, params=params, timeout=30)
                response.raise_for_status()

                soup = BS(response.text, 'html.parser')
                
                id_list = [li['data-goods-no'] for li in soup.find_all('li', {'data-goods-no': True})]
                
                for goods_no in id_list:
                    result = self._parse_search_result(goods_no)
                    if result:
                        results.append(result)
                
            except requests.RequestException as e:
                log.warning(f"Request failed: {e}")
                
        return results


    def _parse_search_result(self, goods_no) -> Optional[MetaRecord]:
        try:
            url = f"{self.BASE_URL}/Goods/{goods_no}"

            response = requests.get(url, timeout=30)
            response.raise_for_status()

            soup = BS(response.text, 'html.parser')
            

            title_element = soup.find('h2', class_='gd_name')
            title = title_element.text.strip() if title_element else ''

            authors = []
            authors_element = soup.find('span', class_='gd_auth')
            if authors_element:
                more_auth_li = authors_element.find('span', class_='moreAuthLi')
                authors = [a.text.strip() for a in (more_auth_li.find_all('a') if more_auth_li 
                        else authors_element.find_all('a', recursive=False))]

            publisher_element = soup.find('span', class_='gd_pub')
            publisher = publisher_element.text.strip() if publisher_element else ''

            pub_date_element = soup.find('span', class_='gd_date')
            pub_date = pub_date_element.text.strip() if pub_date_element else ''
            try:
                if pub_date:
                    pub_date = datetime.strptime(pub_date, "%Y년 %m월 %d일").strftime("%Y-%m-%d")
            except ValueError:
                pass

            isbn13_element = soup.find('th', text='ISBN13')
            isbn13 = isbn13_element.find_next_sibling('td').text.strip() if isbn13_element else ''

            description_element = soup.find('div', class_='infoWrap_txtInner')
            description = description_element.get_text("\n", strip=True) if description_element else ''

            # rating
            rating_element = soup.find('span', class_='gd_rating')
            rating = None
            if rating_element and rating_element.find('em'):
                rating_text = rating_element.find('em').text.strip()
                try:
                    if rating_text:
                        rating = float(rating_text)
                        rating = max(0, min(5, round(rating / 2)))
                except ValueError:
                    pass

            # tags
            tags = []
            infoset_goodsCate = soup.find('div', id='infoset_goodsCate')
            if infoset_goodsCate:
                tags_element = infoset_goodsCate.find('ul', class_='yesAlertLi')
                if tags_element and tags_element.find('li'):
                    tags = [a.text.strip() for a in tags_element.find('li').find_all('a')]

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
                identifiers = {
                    "isbn": isbn13,
                    "Yes24": goods_no
                    },
                languages = ["한국어"]
            )

            return match
        
        except requests.RequestException as e:
            log.warning(f"Failed to parse search result for {goods_no}: {e}")
            return None
