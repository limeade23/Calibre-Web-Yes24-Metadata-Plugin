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
            
            # title
            title = soup.find('h2', class_='gd_name').text.strip()
            
            # authors
            authors_element = soup.find('span', class_='gd_auth')
            authors = [a.text.strip() for a in authors_element.find_all('a')]
            
            # publisher
            publisher = soup.find('span', class_='gd_pub').text.strip()
            
            # publishedDate
            pub_date = soup.find('span', class_='gd_date').text.strip()
            
            # isbn
            isbn13 = soup.find('th', text='ISBN13').find_next_sibling('td').text.strip()
            
            # rating
            rating_element = soup.find('span', class_='gd_rating')
            if rating_element:
                rating_text = rating_element.find('em').text.strip()
                rating = float(rating_text) if rating_text else None
                if rating is not None:
                    rating = max(0, min(5, round(rating / 2)))  # Convert rating from 0-10 to 0-5 range and round to the nearest integer
            else:
                rating = None

            # description
            description_element = soup.find('div', class_='infoWrap_txtInner')
                        
            if description_element:
                description_text = description_element.get_text("\n", strip=True)
            else:
                description_text = ""
                
            # tags
            infoset_goodsCate = soup.find('div', id='infoset_goodsCate')
            tags_element = infoset_goodsCate.find('ul', class_='yesAlertLi').find('li')
            tags = [a.text.strip() for a in tags_element.find_all('a')]
            
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
                description = description_text,
                publisher = publisher,
                publishedDate = datetime.strptime(pub_date, "%Y년 %m월 %d일").strftime("%Y-%m-%d"),
                rating = rating,
                tags = tags,
                identifiers = {
                    "isbn": isbn13,
                    "Yes24": goods_no
                    }
                languages = "한국어"
            )

            return match
        
        except requests.RequestException as e:
            log.warning(f"Failed to parse search result for {goods_no}: {e}")
            return None
