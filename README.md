# Calibre Web Yes24 Metadata Source Plugin

캘리버 웹 Yes24 메타데이터 소스 플러그인


<img width="500" alt="image" src="https://github.com/limeade23/Calibre-Web-Yes24-Metadata-Plugin/assets/143329549/1e8963e1-a9a6-40ef-a929-fa9156b78a36">


<img width="500" alt="image" src="https://github.com/limeade23/Calibre-Web-Yes24-Metadata-Plugin/assets/143329549/4520c7c9-9779-4b3d-9a58-64e7b7754236">



## 설치 방법
1. `./cps/metadata_provider/` 디렉토리에 `yes24.py` 파일을 업로드 한다.

2. docker를 사용한다면 `metadata_provider` 디렉토리를 매핑하거나 `yes24.py` 파일을 매핑해주는 방식 등을 이용한다.
    linuxserver의 이미지를 이용한다면 다음과 같이 추가할 수 있다.
    ```docker-compose
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
    - 설명