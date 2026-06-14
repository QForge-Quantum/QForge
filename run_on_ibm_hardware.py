from qiskit_ibm_runtime import QiskitRuntimeService
from qiskit import qasm3

# 1. Authenticate with your IBM account
service = QiskitRuntimeService(channel="ibm_quantum_platform", token="")

# 2. Select a real physical backend
backend = service.least_busy(operational=True, simulator=False, min_num_qubits=5)
print(f"Running on physical hardware: {backend.name}")

qforge_qasm3_output = """
OPENQASM 3.0;
include "stdgates.inc";

// Target Hardware constraints loaded: ibm_quito
qubit[3] data;
bit[2] syndrome;
bit[3] final_meas;  // <--- NEW: Classical bucket for the final result

h data[0];
cx data[0], data[1];
// Auto-Router bridging Physical Qubit 0 -> 2
swap data[0], data[1];
cx data[1], data[2];
swap data[0], data[1];
x data[1];
qubit[2] anc;
// Auto-Router bridging Physical Qubit 0 -> 3
swap data[0], data[1];
cx data[1], anc[0];
swap data[0], data[1];
cx data[1], anc[0];
// Auto-Router bridging Physical Qubit 1 -> 4
swap data[1], anc[0];
cx anc[0], anc[1];
swap data[1], anc[0];
// Auto-Router bridging Physical Qubit 2 -> 4
swap data[2], data[1];
swap data[1], anc[0];
cx anc[0], anc[1];
swap data[1], anc[0];
swap data[2], data[1];
syndrome[0] = measure anc[0];
syndrome[1] = measure anc[1];

if (syndrome == 3) {
    x data[1];
}

// Final state measurement of register 'data'
final_meas = measure data; 
"""
qc = qasm3.loads(qforge_qasm3_output)

from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
pm = generate_preset_pass_manager(backend=backend, optimization_level=0)
isa_qc = pm.run(qc)

# 5. Submit the ISA-compliant circuit to the physical hardware
from qiskit_ibm_runtime import SamplerV2 as Sampler
sampler = Sampler(mode=backend)

# Notice we are sending 'isa_qc' instead of 'qc' now!
job = sampler.run([isa_qc], shots=1024)

print(f"Job ID: {job.job_id()}")
print("Job submitted to queue! Waiting for results (this might take a few minutes)...")
result = job.result()

# 6. Print the hardware results!
pub_result = result[0]
print("\n--- Hardware Results ---")
print("Syndrome Measurements (Should mostly be '11' because we forced an error):")
print(pub_result.data.syndrome.get_counts())
print("\nFinal Data State (Should mostly be '000' or '111' if QEC worked!):")
print(pub_result.data.final_meas.get_counts())
