# Calibre Web Yes24 Metadata Source Plugin

캘리버 웹 Yes24 메타데이터 소스 플러그인

<img width="500" alt="image" src="https://github.com/user-attachments/assets/1fda9830-c1cb-412b-9d60-9e9101d4d7b9">
<img width="500" alt="image" src="https://github.com/user-attachments/assets/a129ffd6-2391-43f2-b7ae-5ba1df134577">


## 설치 방법
1. `./cps/metadata_provider/` 디렉토리에 `yes24.py` 파일을 업로드 한다.  

2. docker를 사용한다면 `metadata_provider` 디렉토리를 매핑하거나 `yes24.py` 파일을 매핑해주는 방식 등을 이용한다.  
    linuxserver의 이미지를 이용한다면 `docker-compose` 파일에 다음과 같이 추가할 수 있다.  
    ```docker-compose
      volumes:
        ...    
        - ./yes24.py:/app/calibre-web/cps/metadata_provider/yes24.py
    ```

### 참고사항
- 언어는 한국어로 고정되어 있습니다.  
- 불러오는 항목  
    - 제목
    - 저자
    - 출판사
    - 출판일
    - 커버 이미지
    - 평점
    - 설명
- 검색 결과는 일반 도서와 ebook 상품이 모두 표시됩니다. <br> (카테고리(태그)에 신경쓸 경우 일반 도서로 선택해야 합니다. <br> 검색 결과 제목을 누르면 Yes24 상품 페이지로 이동됩니다.)
