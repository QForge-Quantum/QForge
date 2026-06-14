# QForge ⚛️
**An Intent-Driven, Memory-Safe Quantum Compiler Architecture for the NISQ Era.**

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](https://www.python.org/)
[![Target](https://img.shields.io/badge/Target-OpenQASM_3.0-orange)](#)

**QForge** is a domain-specific quantum programming language and semantic compiler. Instead of relying on bottom-up gate cancellation like traditional transpilers, QForge operates at the Abstract Syntax Tree (AST) level. This semantic approach enables intent-driven algorithmic optimization, automated topological hardware routing, and strict compile-time physics tracking to prevent quantum memory leaks.

## 🚀 Key Features

* **Automated Quantum Garbage Collection:** The `with ancilla` context block automatically uncomputes temporary scratchpad qubits. QForge strictly tracks Type-States (Clean, Superposed, Entangled, Measured) to mathematically guarantee the No-Cloning theorem and prevent non-unitary partial trace collapses.
* **Intent-Driven Optimization:** Using the `@optimize` decorator, QForge dynamically swaps underlying algorithmic synthesis (e.g., Draper QFT vs. Ripple-Carry adders) based on hardware depth or width constraints before gate synthesis begins.
* **Topological Auto-Routing:** Abstract hardware constraints away. Declare `@target_hardware("ibm_quito")` and QForge will use shortest-path graph theory to automatically weave `SWAP` networks across disconnected qubits while maintaining logical array readability.
* **Hardware-Native Dynamic Circuits:** Pure `if classical(...)` conditions compile directly into OpenQASM 3.0, pushing real-time feed-forward logic (like Quantum Error Correction) directly to cryogenic FPGA controllers.
* **Integrated Studio IDE:** A standalone GUI featuring real-time AST linting, live DAG circuit drawing, and a local mathematical execution engine capable of calculating the partial trace of interactive single-qubit density matrices.

---

## 🛠️ Installation

Clone the repository and install the minimal dependencies:

```bash
git clone https://github.com/QForge-Quantum/QForge.git
cd qforge
pip install -r requirements.txt

```

*(Dependencies: `qiskit`, `lark`, `networkx`)*

---

## ⚡ Quick Start: Quantum Error Correction

QForge natively supports mid-circuit measurements and classical feed-forward control flow without clunky Python context managers.

Create a file named `qec.qf`:

```python
@target_hardware("ibm_quito")

data = qregister(3)
syndrome = cint(2)
anc = qregister(2)

# ... (State Encoding omitted for brevity) ...

# Mid-Circuit Syndrome Extraction
entangle(data[0], anc[0])
entangle(data[1], anc[0])
syndrome[0] = measure(anc[0])

# Native FPGA-Level Feed-Forward Correction
if classical(syndrome[0] == 1):
    flip(data[0])
end

```

Compile it directly to physical hardware assembly:

```bash
python qforge.py qec.qf --target qasm3

```

**Compiler Output:**

```qasm
OPENQASM 3.0;
include "stdgates.inc";

// Target Hardware constraints loaded: ibm_quito
qubit[3] data;
bit[2] syndrome;
qubit[2] anc;

cx data[0], anc[0];
cx data[1], anc[0];
syndrome[0] = measure anc[0];

if (syndrome[0] == 1) {
    x data[0];
}

```

---

## 🖥️ QForge Studio (The IDE)

To launch the interactive visual development environment:

```bash
python qforge_studio.py

```

* **Type-State Linting:** Errors (like measuring a collapsed wave function) are caught natively in the editor.
* **Live DAG View:** Watch the quantum circuit synthesize in real-time as you type.
* **Graceful Fallback:** If your circuit utilizes dynamic control flow, the IDE will automatically halt local matrix simulation and prompt you to export the QASM for physical QPU execution.

---

## 🏗️ The Compiler Architecture

QForge is built on a 3-stage modular pipeline:

1. **The Lexer/Parser (`Lark`):** Parses the high-level DSL and executes recursive compile-time mathematical evaluations.
2. **The Semantic Analyzer:** A physics-aware visitor pattern that maintains variable environments, loop unrolling, reverse-lookup hardware maps, and strict quantum state tracking.
3. **The Multi-Target Emitter:**
* `QiskitEmitter`: Generates visual circuits and idealized simulations.
* `QASM3Emitter`: Generates bare-metal, unrolled, hardware-agnostic strings for physical execution.



---

## 📜 License & Academic Citation

QForge is open-source and released under the **Apache License 2.0**, ensuring it is safe for both academic research and enterprise integration (including explicit patent grants).

If you use QForge's semantic optimizer or routing algorithms in your research, please cite our whitepaper:

```bibtex
@article{ojas2026qforge,
  title={QForge: An Intent-Driven, Memory-Safe Quantum Compiler Architecture for the NISQ Era},
  author={Ojas Gupta},
  journal={quantum: to_be_edited},
  year={2026}
}
