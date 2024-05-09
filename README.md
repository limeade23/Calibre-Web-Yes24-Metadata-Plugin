# Calibre Web Yes24 Metadata Source Plugin

캘리버 웹 Yes24 메타데이터 소스 플러그인

## 설치 방법
1. `./cps/metadata_provider/` 디렉토리에 `yes24.py` 파일을 업로드 한다.

2. docker-compose를 사용한다면 다음과 같이 `yes24.py` 파일을 매핑해준다.
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