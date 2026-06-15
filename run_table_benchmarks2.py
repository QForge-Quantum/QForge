# run_table_benchmarks.py
import os
import time
import subprocess
import shutil
import numpy as np
import random
import math
from qiskit import QuantumCircuit, transpile
from qiskit.transpiler import CouplingMap
from qforge_compiler import compile_qforge

# Native OS-level memory tracking
try:
    import psutil
    def get_mem(): return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
except ImportError:
    print("Please run: pip install psutil")
    exit()

NUM_RUNS = 3 # Keep low for 127-qubit runs so it doesn't take all day!

def generate_grid_edges(size):
    cols = int(math.sqrt(size))
    rows = math.ceil(size / cols)
    edges = []
    for r in range(rows):
        for c in range(cols):
            idx = r * cols + c
            if c < cols - 1 and idx + 1 < size: edges.append((idx, idx + 1))
            if r < rows - 1 and idx + cols < size: edges.append((idx, idx + cols))
    return edges

# ==========================================
# EXPERIMENT 1: PEAK RAM SCALING
# ==========================================
def run_memory_benchmark(sizes):
    print(f"\n{'='*50}\n1. PEAK MEMORY BENCHMARK (VQE Ansatz Depth=20)\n{'='*50}")
    depth = 20

    for num_qubits in sizes:
        print(f"\n--- Testing Size: {num_qubits} Qubits ---")
        qiskit_mbs, qforge_mbs = [], []

        # 1. Qiskit Pre-build
        qc = QuantumCircuit(num_qubits)
        for _ in range(depth):
            for i in range(num_qubits): qc.ry(1.57, i)
            for i in range(num_qubits - 1): qc.cx(i, i + 1)
        qc.measure_all()

        # 2. QForge Pre-build
        qf_source = [f"q = qregister({num_qubits})"]
        for _ in range(depth):
            for i in range(num_qubits): qf_source.append(f"rotate(q[{i}], \"y\", 1.57)")
            for i in range(num_qubits - 1): qf_source.append(f"entangle(q[{i}], q[{i+1}])")
        qf_source.append("measure(q)")
        qf_code = "\n".join(qf_source)

        for _ in range(NUM_RUNS):
            # Qiskit
            mem_before = get_mem()
            transpile(qc, basis_gates=['rz', 'sx', 'x', 'cx', 'measure'], optimization_level=3)
            qiskit_mbs.append(max(0.1, get_mem() - mem_before))

            # QForge
            mem_before = get_mem()
            compile_qforge(qf_code, target="qasm3")
            qforge_mbs.append(max(0.1, get_mem() - mem_before))

        print(f"   Qiskit Peak RAM : {np.mean(qiskit_mbs):>6.2f} MB (±{np.std(qiskit_mbs):.2f})")
        print(f"   QForge Peak RAM : {np.mean(qforge_mbs):>6.2f} MB (±{np.std(qforge_mbs):.2f})")


# ==========================================
# EXPERIMENT 2: ROUTING LATENCY & SWAPS
# ==========================================
def run_routing_benchmark(sizes):
    print(f"\n{'='*50}\n2. TOPOLOGICAL ROUTING BENCHMARK (Random Pairs)\n{'='*50}")
    
    for num_qubits in sizes:
        print(f"\n--- Testing Size: {num_qubits} Qubits (Utility-Scale Grid) ---")
        
        edges = generate_grid_edges(num_qubits)
        bidirectional_edges = edges + [(v, u) for u, v in edges]
        cmap = CouplingMap(bidirectional_edges)

        # Generate heavily scattered entanglements to stress the router
        random.seed(42) # Fixed seed for reproducibility
        entanglements = [(random.randint(0, num_qubits-1), random.randint(0, num_qubits-1)) for _ in range(num_qubits)]
        entanglements = [(a, b) for a, b in entanglements if a != b] # remove self-loops

        qc = QuantumCircuit(num_qubits)
        for a, b in entanglements: qc.cx(a, b)
        qc.measure_all()
        original_cx_count = qc.count_ops().get('cx', 0)

        qf_source = [f"@target_hardware(\"grid_{num_qubits}\")", f"q = qregister({num_qubits})"]
        for a, b in entanglements: qf_source.append(f"entangle(q[{a}], q[{b}])")
        qf_source.append("measure(q)")
        qf_code = "\n".join(qf_source)

        qiskit_swaps, qforge_swaps = [], []
        qiskit_times, qforge_times = [], []

        for _ in range(NUM_RUNS):
            # --- Qiskit Stochastic Routing ---
            start_time = time.time()
            transpiled_qc = transpile(
                qc, coupling_map=cmap, routing_method="sabre", 
                initial_layout=list(range(num_qubits)), 
                basis_gates=['rz', 'sx', 'x', 'cx', 'measure'], optimization_level=3
            )
            qiskit_times.append(time.time() - start_time)
            routed_cx = transpiled_qc.count_ops().get('cx', 0)
            qiskit_swaps.append((routed_cx - original_cx_count) // 3)

            # --- QForge Deterministic Routing ---
            start_time = time.time()
            qasm_out = compile_qforge(qf_code, target="qasm3")
            qforge_times.append(time.time() - start_time)
            qforge_swaps.append(qasm_out.count("swap ") // 2)

        print(f"   [TIME] Qiskit SABRE   : {np.mean(qiskit_times):>6.3f} sec")
        print(f"   [TIME] QForge Semantic: {np.mean(qforge_times):>6.3f} sec")
        print(f"   [SWAP] Qiskit Count   : {np.mean(qiskit_swaps):>6.1f} (±{np.std(qiskit_swaps):.1f})")
        print(f"   [SWAP] QForge Count   : {np.mean(qforge_swaps):>6.1f} (±{np.std(qforge_swaps):.1f})")

if __name__ == "__main__":
    # Test NISQ (16), Utility (127), Osprey (433), and Condor (1121) scales
    test_sizes = [16, 127, 433, 1121] 
    run_memory_benchmark(test_sizes)
    run_routing_benchmark(test_sizes)
