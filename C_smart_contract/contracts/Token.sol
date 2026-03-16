// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Token {
    // ── ERC-20 기본 ───────────────────────────────────────────
    string public name = "FraudDetectionToken";
    string public symbol = "FDT";
    uint8 public decimals = 18;
    uint256 public totalSupply;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    // ── 에스크로 구조 ─────────────────────────────────────────
    enum EscrowStatus { Pending, Approved, Rejected }

    struct EscrowTx {
        address sender;     // 송신자
        address receiver;   // 수신자
        uint256 amount;     // 잠긴 토큰 수량
        EscrowStatus status;
    }

    // txId => EscrowTx
    mapping(uint256 => EscrowTx) public escrows;
    uint256 public nextTxId;

    // 에스크로 컨트랙트 소유자 (AI 승인/거부 권한)
    address public owner;

    event EscrowDeposited(uint256 indexed txId, address indexed sender, address indexed receiver, uint256 amount);
    event EscrowApproved(uint256 indexed txId, address indexed receiver, uint256 amount);
    event EscrowRejected(uint256 indexed txId, address indexed sender, uint256 amount);

    modifier onlyOwner() {
        require(msg.sender == owner, "Only owner can call this");
        _;
    }

    // ── 생성자 ────────────────────────────────────────────────
    constructor(uint256 _initialSupply) {
        totalSupply = _initialSupply * (10 ** uint256(decimals));
        balanceOf[msg.sender] = totalSupply;
        owner = msg.sender;
        emit Transfer(address(0), msg.sender, totalSupply);
    }

    // ── ERC-20 기본 함수 ──────────────────────────────────────
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

    // ── 에스크로 함수 ─────────────────────────────────────────

    /**
     * @dev 송신자가 토큰을 컨트랙트에 예치 (잠금)
     *      AI 검증이 완료될 때까지 토큰은 컨트랙트가 보관
     * @param _receiver 최종 수신자 주소
     * @param _amount   전송할 토큰 수량 (단위: wei, 즉 10^18 기준)
     * @return txId     이 에스크로 거래의 고유 ID
     */
    function escrowDeposit(address _receiver, uint256 _amount) public returns (uint256 txId) {
        require(_receiver != address(0), "Invalid receiver");
        require(balanceOf[msg.sender] >= _amount, "Insufficient balance");

        // 송신자 잔액에서 차감 → 컨트랙트(address(this))로 이동
        balanceOf[msg.sender] -= _amount;
        balanceOf[address(this)] += _amount;

        txId = nextTxId;
        escrows[txId] = EscrowTx({
            sender:   msg.sender,
            receiver: _receiver,
            amount:   _amount,
            status:   EscrowStatus.Pending
        });
        nextTxId++;

        emit EscrowDeposited(txId, msg.sender, _receiver, _amount);
        return txId;
    }

    /**
     * @dev AI가 정상 판별 → 컨트랙트에서 수신자에게 토큰 전송
     * @param _txId 승인할 에스크로 거래 ID
     */
    function escrowApprove(uint256 _txId) public onlyOwner {
        EscrowTx storage eTx = escrows[_txId];
        require(eTx.status == EscrowStatus.Pending, "Not pending");

        eTx.status = EscrowStatus.Approved;
        balanceOf[address(this)] -= eTx.amount;
        balanceOf[eTx.receiver]  += eTx.amount;

        emit Transfer(address(this), eTx.receiver, eTx.amount);
        emit EscrowApproved(_txId, eTx.receiver, eTx.amount);
    }

    /**
     * @dev AI가 사기 판별 → 컨트랙트에서 송신자에게 토큰 반환
     * @param _txId 거부할 에스크로 거래 ID
     */
    function escrowReject(uint256 _txId) public onlyOwner {
        EscrowTx storage eTx = escrows[_txId];
        require(eTx.status == EscrowStatus.Pending, "Not pending");

        eTx.status = EscrowStatus.Rejected;
        balanceOf[address(this)] -= eTx.amount;
        balanceOf[eTx.sender]    += eTx.amount;

        emit Transfer(address(this), eTx.sender, eTx.amount);
        emit EscrowRejected(_txId, eTx.sender, eTx.amount);
    }

    /**
     * @dev 에스크로 거래 상태 조회
     */
    function getEscrow(uint256 _txId) public view returns (
        address sender,
        address receiver,
        uint256 amount,
        EscrowStatus status
    ) {
        EscrowTx storage eTx = escrows[_txId];
        return (eTx.sender, eTx.receiver, eTx.amount, eTx.status);
    }
}
