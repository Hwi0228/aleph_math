# HEXORA (헥소라)

문제를 등록하고 과목·폴더별로 관리하는 웹 앱입니다.

## 계정

이메일과 비밀번호로 회원가입/로그인합니다. 비밀번호는 12자 이상이어야 하고 단방향 해시로 저장됩니다. 첫 번째 가입 계정이 관리자가 되며, 기존 문제와 폴더는 첫 계정에 연결됩니다. 문제는 모든 로그인 회원에게 공개되는 공동 문제 은행으로 운영하고, 원작성자 정보는 기여 이력으로 보존합니다. 폴더는 사용자별 개인 분류로 유지합니다. 관리자는 **계정별 데이터 관리**에서 작성자별 문제를 필터링할 수 있습니다.

운영 배포에서는 세션 서명 키를 `FLASK_SECRET_KEY` 환경 변수로 설정하고, HTTPS를 쓰면 `FLASK_SESSION_COOKIE_SECURE=true`로 지정하세요. 개발 환경에서는 키를 `instance/.secret_key`에 자동 생성하고 Git에서 제외합니다.

## 문제 DB

Flask 앱을 실행한 다음 **문제 DB 등록**을 눌러 과목, 학년, 단원, 지문, 문제 내용, 보기, 선택지, 답, 출처를 입력할 수 있습니다. 수식은 `$...$`, `\(...\)`, `$$...$$`, `\[...\]` 형식으로 작성하며 KaTeX로 미리 보고 렌더링합니다. KaTeX는 jsDelivr CDN에서 불러옵니다.

로그인 후 대시보드에서 전체 공개 문항 수와 과목·단원별 현황, 최근 등록 문제를 확인할 수 있습니다. 개인 폴더 수는 본인 계정 기준으로 표시합니다.

등록된 문제는 설정한 데이터베이스에 저장됩니다. 기본값은 SQLite의 `instance/probocr.sqlite3`이며 기존 SQLite 마이그레이션 동작을 그대로 사용합니다. PostgreSQL을 선택하면 동일한 문제 은행·회원·폴더·수정 이력·임시 저장·개인 메모 스키마를 자동으로 준비합니다. DB 종류는 실행 환경 변수 DATABASE_TYPE으로 선택하며, SQLite와 PostgreSQL은 서로 별도의 데이터 저장소입니다. DB 종류를 바꿔도 기존 데이터가 자동 복사되지는 않으므로 실제 데이터를 옮기려면 별도 백업·이관 절차가 필요합니다.

문제 DB 왼쪽의 **＋ 새 폴더**에서 폴더와 하위 폴더를 만들 수 있습니다. 폴더는 UUID로 식별되고 사용자별로 분리됩니다. 문항을 공개한 뒤 문제 카드의 **문제 더보기**에서 본인 소유 폴더로 이동할 수 있습니다. 개인 메모도 같은 메뉴에서 관리하며, 해당 메모는 작성한 사용자에게만 표시됩니다. 폴더를 선택하면 그 아래 하위 폴더의 문제도 함께 조회됩니다.

## 협업형 공개 문제 DB

기존 `questions` 테이블을 그대로 공개 문제의 단일 원본으로 사용합니다. 개인 문제를 별도 위키 테이블에 복제하지 않으며, 새 문제와 기존 문제 모두 모든 로그인 회원이 조회하고 편집할 수 있습니다. 원작성자(`user_uuid`)는 유지되고 개인 폴더 관리 권한은 원작성자에게만 있습니다.

문제 카드의 **편집**에서 누구나 문항을 수정할 수 있습니다. 수정 요약, 편집자, 저장 시각과 전체 문항 스냅샷은 버전 이력으로 기록하며, 버전 비교와 이전 버전 복원을 지원합니다. 다른 사용자가 먼저 수정한 문서를 오래된 화면에서 덮어쓰지 않도록 기준 버전 번호를 검사합니다.

기존 문항 행은 이동하거나 복제하지 않습니다. 앱 시작 시 `question_revisions` 테이블을 `CREATE TABLE IF NOT EXISTS`로 추가하고, 이력이 없는 기존 문항은 이력 페이지를 처음 열거나 수정할 때 원본 버전을 자동 기록합니다. 따라서 기존 `questions` 테이블을 다시 만들거나 기존 문제 데이터를 마이그레이션할 필요가 없습니다.

문항을 공개 문제 은행에 등록하거나 수정할 때는 원본 자료를 공유·수정할 권리가 있는지 확인해야 합니다. 특히 변경금지(ND) 조건이 붙은 자료는 문항을 분리·각색해 공동 편집용으로 게시할 수 있다고 가정하지 않습니다.

## HWP 문항 파싱

로그인한 뒤 사이드바의 **HWP 파싱**에서 `.hwp` 또는 `.hwpx`를 업로드하면 `rhwp-python`으로 문서 전체를 파싱하고 구조화 IR을 통해 문항을 추출합니다. 기존 정답·해설 표 형식뿐 아니라 `1.`, `1)`, `1번`, `Q1.` 등으로 시작하는 일반 문단형 시험지도 지원하며, 정답과 해설이 문서에 없는 경우에도 문제와 선택지를 추출할 수 있습니다. 여러 섹션을 순회하며, 업로드 파일은 서버에 보관하지 않습니다. 단독 HWPML `section0.xml`은 기존 호환 파서로 처리합니다.

수식 블록은 rhwp IR의 원본 HWP 수식 스크립트를 읽고 기존 `hml-equation-parser`를 LaTeX 변환 보조기로 사용합니다. 그림 블록은 문항 텍스트에 위치 표시자로 남깁니다. 실제 그림 파일을 문제 DB에 저장하는 기능은 아직 포함하지 않습니다. 파싱 결과에서 등록할 문항을 고르고 과목·학년·단원·출처 유형·출처·연도·라이선스 등의 공통 정보를 입력하면 공개 문제 DB에 일괄 등록됩니다. HWP에서 가져온 각 문항은 최초 버전 이력도 함께 생성됩니다. 업로드·추출 단계와 오류는 화면의 처리 로그에 표시됩니다.

에듀넷 등 외부 자료는 내려받을 수 있다는 사실만으로 재배포 권한이 생기지 않습니다. 특히 출처표시·비영리·변경금지 조건이 있는 자료는 추출 결과를 외부에 공개하기 전에 원본 제공자의 이용 조건과 별도 허락 필요 여부를 확인해야 합니다.

## 화면 디자인

전체 화면은 Academic Blue 기반의 Academic Knowledge Workspace 디자인을 공유합니다. `static/styles/tokens.css`, `static/styles/base.css`, `static/styles/layout.css`, `static/styles/components.css`가 디자인 토큰과 공통 요소를 담당하며, `static/styles/pages/` 아래 파일은 화면별 레이아웃을 정의합니다. 공통 사이드바와 페이지 헤더는 `static/templates/partials/`의 Jinja partial로 재사용합니다.

## 테스트

전체 자동 테스트는 다음 명령으로 실행합니다.

```powershell
python -m unittest discover -s tests -v
```

현재 테스트는 SQLite 기반 회귀 테스트와 PostgreSQL 연결 어댑터·설정·스키마 단위 테스트를 포함합니다. PostgreSQL 테스트 단위 테스트는 외부 DB 접속 없이 실행됩니다. GitHub Actions에서도 PR 및 기본 브랜치 푸시 시 동일한 테스트를 수행합니다.

### Windows 로컬 실행

PR 브랜치를 내려받은 뒤 Python 3.11 가상환경에서 의존성을 설치합니다.

```powershell
# GitHub CLI가 설치되어 있고 인증된 경우
gh pr checkout 13

# 또는 GitHub CLI 없이 PR 브랜치를 가져오는 경우
git fetch origin pull/13/head:pr-13
git switch pr-13

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

PowerShell에서 가상환경 활성화가 차단되면 현재 터미널에 한해 다음을 실행한 뒤 다시 활성화합니다.

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

기본 SQLite 설정으로 테스트와 웹 앱을 실행합니다.

```powershell
$env:DATABASE_TYPE = "sqlite"
$env:DATABASE_PATH = "instance/probocr.sqlite3"
$env:FLASK_SECRET_KEY = "local-development-only-change-me"

python -m unittest discover -s tests -v
python app.py
```

브라우저에서 `http://127.0.0.1:5000`을 열고 회원가입 후 문제 등록, 편집, 폴더, 임시 저장, HWP 가져오기를 확인합니다. SQLite DB 파일은 `instance/probocr.sqlite3`에 생성됩니다.

### Neon PostgreSQL로 로컬 실행

운영 데이터와 분리해서 확인하려면 Neon 콘솔에서 `production` 브랜치의 자식 브랜치(예: `local-test`)를 만든 뒤 그 브랜치의 **pooled connection string**을 복사합니다. 아래 환경 변수는 현재 PowerShell 창에만 적용됩니다.

```powershell
$env:DATABASE_TYPE = "postgresql"
$env:DATABASE_URL = "postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require"
$env:FLASK_SECRET_KEY = "local-development-only-change-me"

python -m unittest discover -s tests -v
python app.py
```

PostgreSQL 백엔드를 선택하면 첫 요청 때 HEXORA 테이블과 인덱스가 생성됩니다. 별도 테스트 브랜치를 사용하면 실제 `production` 데이터에 영향을 주지 않고 회원가입·문제 등록·수정 이력을 확인할 수 있습니다. 테스트가 끝나고 SQLite로 돌아갈 때는 새 PowerShell 창을 열거나 다음처럼 환경 변수를 바꿉니다.

```powershell
$env:DATABASE_TYPE = "sqlite"
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
python app.py
```

`.env.example`은 설정 예시 파일이며 앱이 자동으로 읽지는 않습니다. 접속 문자열을 코드나 Git에 저장하지 말고, 로컬 환경 변수 또는 배포 플랫폼의 비밀 환경 변수로 설정하세요.

## 의존성 및 실행

```powershell
python -m pip install -r requirements.txt
python app.py
```

Flask 진입점은 `app.py`이며, DB, 인증, 문제/폴더, HWP 파싱은 각각 `database.py`, `auth.py`, `question_routes.py`, `hwp_routes.py`에 있습니다.

> 본 제품은 한글과컴퓨터의글 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.


## 데이터베이스 설정

데이터베이스 종류는 앱 화면이 아니라 배포 환경 변수로 설정합니다. 별도 설정이 없으면 기존 SQLite가 기본값입니다.

### SQLite (기본값)

```env
DATABASE_TYPE=sqlite
DATABASE_PATH=instance/probocr.sqlite3
```

DATABASE_PATH는 선택 항목이며 생략하면 instance/probocr.sqlite3를 사용합니다. 기존 SQLite 파일과 자동 마이그레이션 코드는 그대로 유지됩니다.

### PostgreSQL / Neon

```env
DATABASE_TYPE=postgresql
DATABASE_URL=postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require
```

Neon에서는 웹 앱의 일반 쿼리용 pooled connection string을 DATABASE_URL에 설정합니다. 연결 정보는 Git에 올리지 말고 Render의 Environment Variables에 비밀값으로 등록하세요. PostgreSQL 선택 시 최초 요청에서 HEXORA 테이블과 인덱스를 자동 생성합니다.

Render 배포 명령은 Build Command: pip install -r requirements.txt, Start Command: gunicorn app:app을 사용합니다. FLASK_SECRET_KEY도 Render 환경 변수에 고정해 두어야 합니다.

**주의:** SQLite와 PostgreSQL은 같은 데이터를 공유하지 않습니다. DATABASE_TYPE을 바꾸면 앱이 다른 저장소를 사용하며 기존 DB 데이터는 자동 이관되지 않습니다.

## 버전 관리

현재 버전은 `VERSION` 파일을 단일 기준으로 사용합니다. 기존 개발 이력은 기준 버전 `0.0.0`으로 간주하고, 이 버전부터 Semantic Versioning을 적용합니다. 기능 단위 변경은 버전별 PR 하나로 관리하며, 버전 변경 PR에는 `VERSION`과 `CHANGELOG.md`를 함께 갱신합니다. 기능 추가는 MINOR, 하위 호환 수정은 PATCH, 호환성이 깨지는 변경은 MAJOR 버전을 올립니다.

PR을 검토한 뒤 `master`에 병합하면 GitHub Actions가 `VERSION`의 변경 여부를 확인하고, 해당 버전의 `CHANGELOG.md` 항목을 릴리즈 노트로 사용해 `vX.Y.Z` 태그와 GitHub Release를 자동 생성합니다. PR 승인만으로는 릴리즈하지 않으며, 병합이 완료된 뒤에만 실행됩니다. 별도 PAT 없이 워크플로의 `GITHUB_TOKEN`을 사용합니다.

앱 공통 사이드바 하단의 버전 표시는 실행 시 `VERSION` 파일에서 읽습니다. 따라서 `master` 병합 후 호스팅 서비스가 새 코드를 배포하면 사이트에도 동일한 버전이 표시됩니다.
