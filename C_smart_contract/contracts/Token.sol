// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// ─────────────────────────────────────────────────────────────
// FraudDetectionToken (FDT) — ERC-20 기본 토큰
// ─────────────────────────────────────────────────────────────
contract Token {
    string public name = "FraudDetectionToken";
    string public symbol = "FDT";
    uint8 public decimals = 18;
    uint256 public totalSupply;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    constructor(uint256 _initialSupply) {
        totalSupply = _initialSupply * (10 ** uint256(decimals));
        balanceOf[msg.sender] = totalSupply;
        emit Transfer(address(0), msg.sender, totalSupply);
    }

    function transfer(address _to, uint256 _value) public returns (bool success) {
        require(_to != address(0), "Transfer to zero address");
        require(balanceOf[msg.sender] >= _value, "Insufficient balance");

        balanceOf[msg.sender] -= _value;
        balanceOf[_to] += _value;

        emit Transfer(msg.sender, _to, _value);
        return true;
    }

    function approve(address _spender, uint256 _value) public returns (bool success) {
        allowance[msg.sender][_spender] = _value;
        emit Approval(msg.sender, _spender, _value);
        return true;
    }

    function transferFrom(address _from, address _to, uint256 _value) public returns (bool success) {
        require(_to != address(0), "Transfer to zero address");
        require(balanceOf[_from] >= _value, "Insufficient balance");
        require(allowance[_from][msg.sender] >= _value, "Allowance exceeded");

        balanceOf[_from] -= _value;
        balanceOf[_to] += _value;
        allowance[_from][msg.sender] -= _value;

        emit Transfer(_from, _to, _value);
        return true;
    }
}

// ─────────────────────────────────────────────────────────────
// FraudAudit — ZKP(간략화) 사기 탐지 기록 컨트랙트
// AI가 판단한 위험도 점수를 해시화하여 블록에 기록함으로써
// 데이터의 불변성을 유지한다. (2주차)
// ─────────────────────────────────────────────────────────────
contract FraudAudit {
    address public owner;

    // 감사 기록 구조체
    struct AuditRecord {
        address sender;          // 송금 시도자
        address receiver;        // 수신자
        uint256 amount;          // 송금 시도 금액 (wei 단위)
        uint256 fraudScore;      // AI 위험도 점수 (0~10000, 소수점 2자리 * 100)
        bytes32 scoreHash;       // 위험도 점수 + 피처의 keccak256 해시 (ZKP 간략화)
        bool blocked;            // 차단 여부
        uint256 timestamp;       // 기록 시각
    }

    // 전체 감사 기록 배열
    AuditRecord[] public auditLog;

    // 이벤트: 정상 거래 통과
    event TransactionApproved(
        uint256 indexed recordId,
        address indexed sender,
        address indexed receiver,
        uint256 amount,
        uint256 fraudScore,
        bytes32 scoreHash
    );

    // 이벤트: 사기 의심 거래 차단
    event TransactionBlocked(
        uint256 indexed recordId,
        address indexed sender,
        address indexed receiver,
        uint256 amount,
        uint256 fraudScore,
        bytes32 scoreHash
    );

    constructor() {
        owner = msg.sender;
    }

    /// @notice AI FDS 판단 결과를 블록체인에 기록한다
    /// @param _sender    송금 시도자 주소
    /// @param _receiver  수신자 주소
    /// @param _amount    송금 시도 금액
    /// @param _fraudScore AI 위험도 점수 (예: 8732 = 87.32%)
    /// @param _scoreHash  오프체인에서 계산한 keccak256 해시
    /// @param _blocked   차단 여부
    function recordAudit(
        address _sender,
        address _receiver,
        uint256 _amount,
        uint256 _fraudScore,
        bytes32 _scoreHash,
        bool    _blocked
    ) public {
        uint256 recordId = auditLog.length;

        auditLog.push(AuditRecord({
            sender:     _sender,
            receiver:   _receiver,
            amount:     _amount,
            fraudScore: _fraudScore,
            scoreHash:  _scoreHash,
            blocked:    _blocked,
            timestamp:  block.timestamp
        }));

        if (_blocked) {
            emit TransactionBlocked(recordId, _sender, _receiver, _amount, _fraudScore, _scoreHash);
        } else {
            emit TransactionApproved(recordId, _sender, _receiver, _amount, _fraudScore, _scoreHash);
        }
    }

    /// @notice 전체 감사 기록 수 조회
    function getAuditCount() public view returns (uint256) {
        return auditLog.length;
    }

    /// @notice 특정 기록의 해시를 온체인에서 검증
    /// @dev 오프체인 해시와 온체인 저장값을 비교하여 무결성 확인
    function verifyHash(uint256 _recordId, bytes32 _expectedHash) public view returns (bool) {
        require(_recordId < auditLog.length, "Record does not exist");
        return auditLog[_recordId].scoreHash == _expectedHash;
    }
}
