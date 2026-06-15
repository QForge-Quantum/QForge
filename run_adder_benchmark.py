from qiskit import QuantumCircuit, transpile
from qiskit.circuit.library import VBERippleCarryAdder, DraperQFTAdder

def benchmark_adders(num_state_qubits):
    print(f"\n--- Benchmarking {num_state_qubits}-bit Quantum Adders ---")
    
    # 1. Ripple Carry Adder (Standard Gate-Level unrolling)
    rc_adder = VBERippleCarryAdder(num_state_qubits)
    qc_rc = QuantumCircuit(rc_adder.num_qubits)
    qc_rc.append(rc_adder, range(rc_adder.num_qubits))
    transpiled_rc = transpile(qc_rc, basis_gates=['rz', 'sx', 'x', 'cx'], optimization_level=3)
    
    # 2. Draper QFT Adder (QForge's Semantic target)
    qft_adder = DraperQFTAdder(num_state_qubits)
    qc_qft = QuantumCircuit(qft_adder.num_qubits)
    qc_qft.append(qft_adder, range(qft_adder.num_qubits))
    transpiled_qft = transpile(qc_qft, basis_gates=['rz', 'sx', 'x', 'cx'], optimization_level=3)

    print(f"[Ripple-Carry] Total Qubits: {rc_adder.num_qubits}")
    print(f"[Ripple-Carry] Circuit Depth: {transpiled_rc.depth()}")
    print(f"[Ripple-Carry] CNOT Count: {transpiled_rc.count_ops().get('cx', 0)}")
    
    print(f"\n[Draper QFT] Total Qubits: {qft_adder.num_qubits}")
    print(f"[Draper QFT] Circuit Depth: {transpiled_qft.depth()}")
    print(f"[Draper QFT] CNOT Count: {transpiled_qft.count_ops().get('cx', 0)}")

if __name__ == "__main__":
    benchmark_adders(16) # 16-bit arithmetic
