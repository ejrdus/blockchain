# Ganache 실행 가이드

1. 가나슈(Ganache)를 실행합니다.
2. 메인 화면에서 `Quickstart` 버튼을 클릭하여 로컬 체인을 생성합니다.
3. 상단 메뉴의 **RPC SERVER** 주소가 `http://127.0.0.1:7545`인지 확인합니다.
   (만약 포트가 다르다면 `config.py`의 `GANACHE_RPC_URL`을 수정해 주세요.)
4. 터미널에서 `pip install web3` 패키지를 설치합니다.
5. `python read_block.py`를 실행하여 블록의 해시와 넌스 값이 잘 나오는지 확인합니다.

---

## 설정 검증 코드

아래 코드를 터미널에서 실행하면 설정이 올바른지 자동으로 확인합니다.

```python
# ganache_setup 검증 코드
# 사용법: python -c "..."  또는 별도 .py 파일로 저장 후 실행

import subprocess, sys

# 1. web3 설치 확인
try:
    import web3
    print(f"✅ web3 설치 확인 (버전: {web3.__version__})")
except ImportError:
    print("⚠️  web3 미설치 → 자동 설치 시작...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "web3"])
    import web3
    print(f"✅ web3 설치 완료 (버전: {web3.__version__})")

# 2. config 로드
from config import GANACHE_RPC_URL, CHAIN_ID
print(f"✅ RPC URL : {GANACHE_RPC_URL}")

# 3. Ganache 연결 확인
from web3 import Web3
w3 = Web3(Web3.HTTPProvider(GANACHE_RPC_URL))
if not w3.is_connected():
    print(f"❌ 연결 실패: {GANACHE_RPC_URL}")
    print("→ Ganache GUI를 실행하고 Quickstart로 체인을 생성해주세요.")
    sys.exit(1)

print(f"✅ 연결 성공 | Chain ID: {w3.eth.chain_id}")
print(f"✅ 최신 블록: {w3.eth.block_number} | 계정 수: {len(w3.eth.accounts)}개")
print("✅ 모든 설정 완료 → python read_block.py 를 실행하세요.")
```