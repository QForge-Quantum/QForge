# qforge.py
import sys
import os
import argparse
from qforge_compiler import compile_qforge, QuantumCompileError

# Force UTF-8 encoding to support Qiskit's circuit drawing on Windows
if sys.stdout.encoding.lower() != 'utf-8':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

def main():
    # Set up command-line arguments
    parser = argparse.ArgumentParser(description="QForge Quantum Compiler")
    parser.add_argument("filename", help="Path to the .qf source file")
    parser.add_argument("-t", "--target", choices=["qiskit", "qasm3"], default="qiskit",
                        help="The compilation target: 'qiskit' (visual circuit) or 'qasm3' (raw string code). Default is qiskit.")
    
    # Parse the arguments
    args = parser.parse_args()
    filename = args.filename
    
    if not os.path.exists(filename):
        print(f"Error: File '{filename}' not found.")
        sys.exit(1)

    with open(filename, 'r') as file:
        source_code = file.read()

    print(f"--- Compiling {filename} (Target: {args.target}) ---")
    try:
        # Pass the target argument to the compiler
        result = compile_qforge(source_code, target=args.target)
        print("\n[Success] Compilation finished safely.")
        
        # Handle the output based on what the compiler returned
        if args.target == "qasm3":
            print("\n--- OpenQASM 3.0 Code ---")
            print(result)  # Print the raw string
        else:
            print("\n--- Synthesized Circuit ---")
            print(result.draw())  # Draw the Qiskit circuit
            
    except QuantumCompileError as e:
        print(e)
        sys.exit(1)
    except Exception as e:
        print(f"Syntax Error / Compiler Crash: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
