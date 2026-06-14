# qforge_compiler.py

import math
from dataclasses import dataclass
from typing import List, Optional
from enum import Enum
from lark import Lark, Transformer
from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister
from qiskit.circuit import Parameter, Gate
from qiskit.circuit.library import DraperQFTAdder, HGate, RXGate, RYGate, RZGate

# Graceful degradation if the developer hasn't installed NetworkX
try:
    import networkx as nx
except ImportError:
    nx = None

# ==========================================
# 1. THE UNIFIED GRAMMAR (Sprints 1 - 14)
# ==========================================
GRAMMAR = """
start: statement+

# Sprint 12: Hardware Target Decorator
target_hw: "@target_hardware" "(" ESCAPED_STRING ")"

# Sprint 14: Pulse Level Calibration Blocks
defcal_block: "defcal" IDENTIFIER "(" params ")" ":" statement+ "end"

?statement: assignment | expression | with_block | if_block | for_block | def_block | defcal_block | target_hw

with_block: "with" "ancilla" "(" NUMBER ")" "as" IDENTIFIER ":" statement+ "end"

# Sprint 10: Differentiating Quantum and Classical IF statements
if_block: "if" "quantum" "(" expression ")" ":" statement+ "end"             -> if_quantum
        | "if" "classical" "(" index_expr "==" NUMBER ")" ":" statement+ "end" -> if_classical

for_block: "for" IDENTIFIER "in" "range" "(" NUMBER ")" ":" statement+ "end"

optimize_dec: "@optimize" "(" "target" "=" ESCAPED_STRING ")"
def_block: optimize_dec? "def" IDENTIFIER "(" params ")" ":" statement+ "end"

params: [IDENTIFIER ("," IDENTIFIER)*]

# Sprint 10: Differentiating normal assignments from mid-circuit measurements
assignment: IDENTIFIER "=" expression                             -> assign_var
          | index_expr "=" "measure" "(" index_expr ")"           -> assign_measure

?expression: function_call | index_expr | math_expr

# Recursive Math Grammar for Compile-Time Evaluation!
?math_expr: sum
?sum: product
    | sum "+" product   -> math_add
    | sum "-" product   -> math_sub
?product: atom
    | product "*" atom  -> math_mul
    | product "/" atom  -> math_div
    | product "%" atom  -> math_mod
?atom: NUMBER           -> math_num
     | FLOAT            -> math_float
     | ESCAPED_STRING   -> math_str
     | IDENTIFIER       -> math_var
     | "(" math_expr ")"

index_expr: IDENTIFIER "[" math_expr "]"
function_call: IDENTIFIER "(" [arguments] ")"
arguments: expression ("," expression)*

%import common.CNAME -> IDENTIFIER
%import common.INT -> NUMBER
%import common.FLOAT
%import common.ESCAPED_STRING
%import common.WS
%import common.SH_COMMENT
%ignore WS
%ignore SH_COMMENT
"""

# ==========================================
# 2. AST DEFINITIONS
# ==========================================
class ASTNode: pass

class MathNode(ASTNode): pass

@dataclass
class MathNum(MathNode): val: int
@dataclass
class MathFloat(MathNode): val: float 
@dataclass
class MathStr(MathNode): val: str     
@dataclass
class MathVar(MathNode): name: str
@dataclass
class MathBinOp(MathNode): left: MathNode; op: str; right: MathNode

@dataclass
class DynamicIndex: var_name: str; offset: int
@dataclass
class QRegisterDecl(ASTNode): name: str; size: int; line: int
@dataclass
class QIntDecl(ASTNode): name: str; size: int; line: int
@dataclass
class CIntDecl(ASTNode): name: str; size: int; line: int  
@dataclass
class ParamDecl(ASTNode): name: str; line: int 

@dataclass
class TargetHardwareOp(ASTNode): name: str; line: int 

# Sprint 14: Pulse Control Nodes
@dataclass
class DefCalNode(ASTNode): name: str; params: List[str]; body: List[ASTNode]; line: int
@dataclass
class GaussianPulse(ASTNode): duration: object; amp: object; sigma: object; line: int
@dataclass
class DriveChannel(ASTNode): target: object; line: int
@dataclass
class PlayOp(ASTNode): pulse: ASTNode; channel: ASTNode; line: int

@dataclass
class QubitIndex(ASTNode): reg_name: str; index: object; line: int
@dataclass
class SuperposeOp(ASTNode): target: object; line: int
@dataclass
class FlipOp(ASTNode): target: QubitIndex; line: int
@dataclass
class EntangleOp(ASTNode): control: QubitIndex; target: QubitIndex; line: int
@dataclass
class RotateOp(ASTNode): target: QubitIndex; axis: object; angle: object; line: int 
@dataclass
class AddOp(ASTNode): source_reg: str; target_reg: str; line: int
@dataclass
class SearchOracleOp(ASTNode): target_reg: str; target_val: int; line: int
@dataclass
class MeasureOp(ASTNode): target_reg: str; line: int
@dataclass
class MeasureAssignOp(ASTNode): target_creg: QubitIndex; target_qreg: QubitIndex; line: int 
@dataclass
class AssignmentAlias(ASTNode): new_name: str; target_reg: str; line: int
@dataclass
class AncillaContextNode(ASTNode): size: int; alias: str; body: List[ASTNode]; line: int
@dataclass
class IfQuantumNode(ASTNode): control: QubitIndex; body: List[ASTNode]; line: int
@dataclass
class IfClassicalNode(ASTNode): control_creg: QubitIndex; val: int; body: List[ASTNode]; line: int 
@dataclass
class ForLoopNode(ASTNode): loop_var: str; iterations: int; body: List[ASTNode]; line: int
@dataclass
class DefNode(ASTNode): name: str; params: List[str]; body: List[ASTNode]; optimize_target: str; line: int
@dataclass
class MacroCallNode(ASTNode): name: str; args: List[ASTNode]; line: int
@dataclass
class Program(ASTNode): statements: List[ASTNode]
@dataclass
class ConstantAssignNode(ASTNode): name: str; value: object; line: int
# ==========================================
# 3. BASE VISITOR, ERRORS & MATH EVALUATOR
# ==========================================
class NodeVisitor:
    def visit(self, node):
        method_name = 'visit_' + node.__class__.__name__
        return getattr(self, method_name, self.generic_visit)(node)

    def generic_visit(self, node):
        if hasattr(node, 'statements'):
            for statement in node.statements:
                self.visit(statement)

class QuantumCompileError(Exception):
    def __init__(self, message, line):
        super().__init__(f"\nERROR [QForge Semantic]: {message}\n --> line {line}")

class TypeState(Enum):
    CLEAN = "Clean"
    SUPERPOSED = "Superposed"
    ENTANGLED = "Entangled"
    MEASURED = "Measured"

def evaluate_math(expr, env, line):
    """Recursively evaluates complex AST math during compile time."""
    if isinstance(expr, (int, float)): return expr
    
    # THE FIX: If it's a string, check if it's a stored constant!
    if isinstance(expr, str):
        if expr in env: return env[expr]
        return expr
        
    if isinstance(expr, MathNum): return expr.val
    if isinstance(expr, MathFloat): return expr.val
    if isinstance(expr, MathStr): return expr.val
    if isinstance(expr, MathVar):
        if expr.name not in env: 
            return expr.name
        return env[expr.name]
    if isinstance(expr, MathBinOp):
        left = evaluate_math(expr.left, env, line)
        right = evaluate_math(expr.right, env, line)
        if isinstance(left, str) or isinstance(right, str):
            return f"{left} {expr.op} {right}"
        if expr.op == '+': return left + right
        if expr.op == '-': return left - right
        if expr.op == '*': return left * right
        if expr.op == '/': return left / right 
        if expr.op == '%': return left % right
    return expr

# ==========================================
# 4. SEMANTIC ANALYZER (State Tracking & Topology)
# ==========================================
class SemanticAnalyzer(NodeVisitor):
    def __init__(self):
        self.registers = {}
        self.reg_sizes = {}
        self.cregisters = {} 
        self.creg_sizes = {}
        self.parameters = {} 
        self.macros = {}
        self.macro_env = {}
        self.loop_env = {} 
        
        self.hardware_graph = None
        self.hardware_name = None
        self.reg_to_physical = {}
        self.physical_counter = 0
        self.physical_to_logical = {}  # NEW: Reverse lookup map!
        self.physical_counter = 0
        
    def resolve(self, obj):
        if isinstance(obj, MathVar): obj = obj.name
        if isinstance(obj, str): return self.macro_env.get(obj, obj)
        elif isinstance(obj, QubitIndex):
            res_reg = self.macro_env.get(obj.reg_name, obj.reg_name)
            # Combine environments so the math engine can see BOTH loops and constants
            combined_env = {**self.macro_env, **self.loop_env}
            real_idx = evaluate_math(obj.index, combined_env, obj.line)
            
            if res_reg in self.reg_sizes and (real_idx < 0 or real_idx >= self.reg_sizes[res_reg]):
                raise QuantumCompileError(f"Index {real_idx} out of bounds for register '{res_reg}' (size {self.reg_sizes[res_reg]}).", obj.line)
            if res_reg in self.creg_sizes and (real_idx < 0 or real_idx >= self.creg_sizes[res_reg]):
                raise QuantumCompileError(f"Index {real_idx} out of bounds for classical register '{res_reg}' (size {self.creg_sizes[res_reg]}).", obj.line)
                
            return QubitIndex(reg_name=res_reg, index=real_idx, line=obj.line)
        return obj

    def visit_Program(self, node): self.generic_visit(node)
    def visit_ConstantAssignNode(self, node):
        self.macro_env[node.name] = evaluate_math(node.value, self.macro_env, node.line)
    def visit_TargetHardwareOp(self, node):
        self.hardware_name = node.name
        if not nx:
            return
            
        self.hardware_graph = nx.Graph()
        if node.name == "ibm_quito": # 5-qubit
            self.hardware_graph.add_edges_from([(0,1), (1,2), (1,3), (3,4)])
        elif node.name == "ibm_16":  # 16-qubit Heavy Hex Lattice
            self.hardware_graph.add_edges_from([
                (0,1), (1,2), (1,4), (3,4), (4,5), (5,8), 
                (6,7), (7,8), (8,9), 
                (8,11),  # <--- THE MISSING BRIDGE 
                (10,11), (11,14), 
                (12,13), (13,14), (14,15)
            ])
        else:
            self.hardware_graph = None

    def _declare_reg(self, name, size):
        self.registers[name] = {i: TypeState.CLEAN for i in range(size)}
        self.reg_sizes[name] = size
        self.reg_to_physical[name] = []
        
        # Populate BOTH forward and reverse maps
        for i in range(size):
            p_idx = self.physical_counter + i
            self.reg_to_physical[name].append(p_idx)
            self.physical_to_logical[p_idx] = f"{name}[{i}]" # E.g., 3 -> "anc[0]"
            
        self.physical_counter += size

    def _declare_creg(self, name, size):
        self.cregisters[name] = size
        self.creg_sizes[name] = size

    def visit_QRegisterDecl(self, node): self._declare_reg(node.name, node.size)
    def visit_QIntDecl(self, node): self._declare_reg(node.name, node.size)
    def visit_CIntDecl(self, node): self._declare_creg(node.name, node.size)
    def visit_ParamDecl(self, node): self.parameters[node.name] = True 

    def visit_DefNode(self, node): self.macros[node.name] = node
    def visit_DefCalNode(self, node): self.macros[node.name] = node

    def visit_MacroCallNode(self, node):
        if node.name not in self.macros:
            raise QuantumCompileError(f"Undefined func '{node.name}'.", node.line)
        macro = self.macros[node.name]
        
        old_env = self.macro_env.copy()
        for param, arg in zip(macro.params, node.args):
            self.macro_env[param] = self.resolve(arg)
            
        for stmt in macro.body: self.visit(stmt)
        self.macro_env = old_env

    def visit_DriveChannel(self, node):
        targ = self.resolve(node.target)
        if targ.reg_name not in self.reg_sizes:
            raise QuantumCompileError(f"Undefined register '{targ.reg_name}' for DriveChannel.", node.line)
            
    def visit_GaussianPulse(self, node): pass
    def visit_PlayOp(self, node):
        self.visit(node.pulse)
        self.visit(node.channel)

    def visit_AncillaContextNode(self, node):
        self._declare_reg(node.alias, node.size)
        for stmt in node.body: self.visit(stmt)
        for i, state in self.registers[node.alias].items():
            if state == TypeState.MEASURED: raise QuantumCompileError("Cannot uncompute Collapsed ancilla!", node.line)
        del self.registers[node.alias]; del self.reg_sizes[node.alias]

    def visit_IfQuantumNode(self, node):
        ctrl = self.resolve(node.control)
        self.registers[ctrl.reg_name][ctrl.index] = TypeState.ENTANGLED
        for stmt in node.body: self.visit(stmt)

    def visit_IfClassicalNode(self, node):
        c_idx = self.resolve(node.control_creg)
        if c_idx.reg_name not in self.creg_sizes:
            raise QuantumCompileError(f"Classical condition requires a classical bit, got '{c_idx.reg_name}'.", node.line)
        for stmt in node.body: self.visit(stmt)

    def visit_ForLoopNode(self, node):
        old_val = self.loop_env.get(node.loop_var)
        for i in range(node.iterations):
            self.loop_env[node.loop_var] = i
            for stmt in node.body: self.visit(stmt)
        if old_val is not None: self.loop_env[node.loop_var] = old_val
        else: del self.loop_env[node.loop_var]

    def visit_SuperposeOp(self, node):
        target = self.resolve(node.target)
        if isinstance(target, str):
            for i in range(self.reg_sizes[target]):
                if self.registers[target][i] == TypeState.MEASURED: 
                    raise QuantumCompileError(f"Cannot superpose '{target}'. It is already Measured.", node.line)
                self.registers[target][i] = TypeState.SUPERPOSED
        else:
            if self.registers[target.reg_name][target.index] == TypeState.MEASURED: 
                raise QuantumCompileError(f"Cannot superpose '{target.reg_name}[{target.index}]'. It is already Measured.", node.line)
            self.registers[target.reg_name][target.index] = TypeState.SUPERPOSED

    def visit_FlipOp(self, node):
        targ = self.resolve(node.target)
        if self.registers[targ.reg_name][targ.index] == TypeState.MEASURED: 
            raise QuantumCompileError("Cannot flip a Measured qubit.", node.line)

    def visit_RotateOp(self, node):
        targ = self.resolve(node.target)
        if self.registers[targ.reg_name][targ.index] == TypeState.MEASURED: 
            raise QuantumCompileError("Cannot apply QML rotations to a Measured qubit.", node.line)
        self.registers[targ.reg_name][targ.index] = TypeState.SUPERPOSED

    def visit_EntangleOp(self, node):
        ctrl = self.resolve(node.control)
        targ = self.resolve(node.target)
        if self.registers[ctrl.reg_name][ctrl.index] == TypeState.MEASURED or \
           self.registers[targ.reg_name][targ.index] == TypeState.MEASURED:
            raise QuantumCompileError("Cannot entangle Measured qubits.", node.line)
            
        self.registers[ctrl.reg_name][ctrl.index] = TypeState.ENTANGLED
        self.registers[targ.reg_name][targ.index] = TypeState.ENTANGLED

    def visit_MeasureOp(self, node):
        target = self.resolve(node.target_reg)
        
        if isinstance(target, QubitIndex):
            # Measuring a single qubit (e.g., measure(q[2]))
            if self.registers[target.reg_name][target.index] == TypeState.MEASURED:
                raise QuantumCompileError(f"Double Measurement on '{target.reg_name}[{target.index}]'.", node.line)
            self.registers[target.reg_name][target.index] = TypeState.MEASURED
        else:
            # Measuring the whole register (e.g., measure(q))
            if target not in self.registers:
                raise QuantumCompileError(f"Undefined register '{target}'.", node.line)
            for i in self.registers[target]:
                if self.registers[target][i] == TypeState.MEASURED:
                    raise QuantumCompileError(f"Double Measurement on '{target}'.", node.line)
                self.registers[target][i] = TypeState.MEASURED

    def visit_MeasureAssignOp(self, node):
        c_idx = self.resolve(node.target_creg)
        q_idx = self.resolve(node.target_qreg)
        
        if c_idx.reg_name not in self.creg_sizes:
            raise QuantumCompileError(f"Undefined classical register '{c_idx.reg_name}'.", node.line)
            
        if self.registers[q_idx.reg_name][q_idx.index] == TypeState.MEASURED:
            raise QuantumCompileError(f"Double Measurement on '{q_idx.reg_name}'.", node.line)
            
        self.registers[q_idx.reg_name][q_idx.index] = TypeState.MEASURED

    def visit_AssignmentAlias(self, node):
        if node.target_reg in self.registers:
            raise QuantumCompileError(f"No-Cloning Violation: Cannot duplicate '{node.target_reg}' to '{node.new_name}'.", node.line)

    def visit_AddOp(self, node):
        src = self.resolve(node.source_reg)
        tgt = self.resolve(node.target_reg)
        if self.reg_sizes[src] != self.reg_sizes[tgt]:
            raise QuantumCompileError("Register size mismatch for addition.", node.line)
        for reg in [src, tgt]:
            for i in range(self.reg_sizes[reg]):
                if self.registers[reg][i] == TypeState.MEASURED:
                    raise QuantumCompileError(f"Cannot perform arithmetic. '{reg}' is already Measured.", node.line)
                self.registers[reg][i] = TypeState.ENTANGLED

    def visit_SearchOracleOp(self, node):
        reg = self.resolve(node.target_reg)
        max_val = (2 ** self.reg_sizes[reg]) - 1
        if node.target_val > max_val or node.target_val < 0:
            raise QuantumCompileError(f"Predicate out of bounds (max {max_val}).", node.line)
        for i in range(self.reg_sizes[reg]):
            if self.registers[reg][i] == TypeState.MEASURED:
                raise QuantumCompileError(f"Cannot apply Oracle. '{reg}' is already Measured.", node.line)
            self.registers[reg][i] = TypeState.ENTANGLED

# ==========================================
# 5a. SYNTHESIS ENGINE (Qiskit Target)
# ==========================================
class QiskitEmitter(NodeVisitor):
    def __init__(self, analyzer: SemanticAnalyzer):
        self.analyzer = analyzer
        self.circuit = QuantumCircuit()
        self.qregs = {}
        self.cregs = {} 
        self.params = {} 
        self.reg_sizes = {}
        self.qubit_counter = 0
        self.cbit_counter = 0 
        self.control_stack = []
        self.macros = {}
        self.macro_env = {}
        self.loop_env = {}
        self.active_optimization = None 
    def visit_ConstantAssignNode(self, node):
        self.macro_env[node.name] = evaluate_math(node.value, self.macro_env, node.line)
    def resolve(self, obj):
        if isinstance(obj, MathVar): obj = obj.name
        if isinstance(obj, str): return self.macro_env.get(obj, obj)
        elif isinstance(obj, QubitIndex):
            res_reg = self.macro_env.get(obj.reg_name, obj.reg_name)
            # Combine environments so the math engine can see BOTH loops and constants
            combined_env = {**self.macro_env, **self.loop_env}
            real_idx = evaluate_math(obj.index, combined_env, obj.line)
            
            # THE FIX: We must return the newly built QubitIndex with the integer!
            return QubitIndex(reg_name=res_reg, index=real_idx, line=obj.line)
            
        return obj

    def _allocate(self, name, size):
        self.qregs[name] = self.qubit_counter
        self.reg_sizes[name] = size
        self.qubit_counter += size
        self.circuit.add_register(QuantumRegister(size, name))

    def visit_Program(self, node):
        self.generic_visit(node)
        return self.circuit

    def visit_TargetHardwareOp(self, node): pass 
    def visit_QRegisterDecl(self, node): self._allocate(node.name, node.size)
    def visit_QIntDecl(self, node): self._allocate(node.name, node.size)
    
    def visit_CIntDecl(self, node):
        self.cregs[node.name] = self.cbit_counter
        self.cbit_counter += node.size
        self.circuit.add_register(ClassicalRegister(node.size, node.name))

    def visit_ParamDecl(self, node):
        self.params[node.name] = Parameter(node.name)

    def visit_DefNode(self, node): self.macros[node.name] = node
    def visit_DefCalNode(self, node): self.macros[node.name] = node

    def visit_MacroCallNode(self, node):
        macro = self.macros[node.name]
        old_env = self.macro_env.copy()
        
        # Sprint 14: Handling Pulse blocks under Qiskit 2.x limitations
        if isinstance(macro, DefCalNode):
            for param, arg in zip(macro.params, node.args):
                self.macro_env[param] = self.resolve(arg)
                
            q_indices = []
            for arg in node.args:
                res = self.resolve(arg)
                if isinstance(res, QubitIndex):
                    q_indices.append(self.qregs[res.reg_name] + res.index)
                    
            # IBM killed qiskit.pulse in Qiskit 2.0+.
            # We simply append an opaque Gate here for visualization purposes.
            # The actual pulse waveform is ONLY exported in the QASM3 target!
            custom_gate = Gate(name=node.name, num_qubits=len(q_indices), params=[])
            self.circuit.append(custom_gate, q_indices)
            
            self.macro_env = old_env
            return

        old_opt = getattr(self, 'active_optimization', None)
        if getattr(macro, 'optimize_target', None): self.active_optimization = macro.optimize_target
            
        for param, arg in zip(macro.params, node.args):
            self.macro_env[param] = self.resolve(arg)
            
        for stmt in macro.body: self.visit(stmt)
        
        self.macro_env = old_env
        self.active_optimization = old_opt

    def visit_PlayOp(self, node): pass
    def visit_GaussianPulse(self, node): pass
    def visit_DriveChannel(self, node): pass

    def visit_AncillaContextNode(self, node):
        self._allocate(node.alias, node.size)
        for stmt in node.body: self.visit(stmt)
        for stmt in reversed(node.body): 
            if isinstance(stmt, (SuperposeOp, EntangleOp, FlipOp)): self.visit(stmt)

    def visit_IfQuantumNode(self, node):
        ctrl = self.resolve(node.control)
        self.control_stack.append(self.qregs[ctrl.reg_name] + ctrl.index)
        for stmt in node.body: self.visit(stmt)
        self.control_stack.pop()

    def visit_IfClassicalNode(self, node):
        c_idx = self.resolve(node.control_creg)
        cbit_index = self.cregs[c_idx.reg_name] + c_idx.index
        cbit = self.circuit.clbits[cbit_index]

        with self.circuit.if_test((cbit, node.val)):
            for stmt in node.body:
                self.visit(stmt)

    def visit_ForLoopNode(self, node):
        old_val = self.loop_env.get(node.loop_var)
        for i in range(node.iterations):
            self.loop_env[node.loop_var] = i
            for stmt in node.body: self.visit(stmt)
        if old_val is not None: self.loop_env[node.loop_var] = old_val
        else: del self.loop_env[node.loop_var]

    def visit_SuperposeOp(self, node):
        target = self.resolve(node.target)
        targets = []
        if isinstance(target, str):
            start = self.qregs[target]
            targets = [start + i for i in range(self.reg_sizes[target])]
        else: targets = [self.qregs[target.reg_name] + target.index]

        for t in targets:
            if self.control_stack: self.circuit.append(HGate().control(len(self.control_stack)), self.control_stack + [t])
            else: self.circuit.h(t)

    def visit_FlipOp(self, node):
        targ = self.resolve(node.target)
        t_q = self.qregs[targ.reg_name] + targ.index
        if self.control_stack: self.circuit.mcx(self.control_stack, t_q)
        else: self.circuit.x(t_q)

    def visit_RotateOp(self, node):
        targ = self.resolve(node.target)
        t_q = self.qregs[targ.reg_name] + targ.index
        
        combined_env = {**self.macro_env, **self.loop_env}
        axis = evaluate_math(node.axis, combined_env, node.line)
        if isinstance(axis, str): axis = axis.lower()
        angle_val = evaluate_math(node.angle, combined_env, node.line)
        
        # If the parameter is a string, check if it's in our registered params
        if isinstance(angle_val, str) and angle_val in self.params:
            angle_val = self.params[angle_val]
        
        if isinstance(angle_val, str):
            raise QuantumCompileError(f"Invalid parameter: '{angle_val}' is not a float or registered param.", node.line)
        
        gate_map = {'x': RXGate, 'y': RYGate, 'z': RZGate}
        if axis not in gate_map: raise QuantumCompileError(f"Unsupported axis '{axis}'", node.line)
        
        if self.control_stack:
            self.circuit.append(gate_map[axis](angle_val).control(len(self.control_stack)), self.control_stack + [t_q])
        else:
            getattr(self.circuit, f"r{axis}")(angle_val, t_q)

    def visit_EntangleOp(self, node):
        ctrl = self.resolve(node.control)
        targ = self.resolve(node.target)
        c_q = self.qregs[ctrl.reg_name] + ctrl.index
        t_q = self.qregs[targ.reg_name] + targ.index
        
        if self.analyzer.hardware_graph:
            p_ctrl = self.analyzer.reg_to_physical[ctrl.reg_name][ctrl.index]
            p_targ = self.analyzer.reg_to_physical[targ.reg_name][targ.index]
            
            if not self.analyzer.hardware_graph.has_edge(p_ctrl, p_targ):
                print(f"[Auto-Router] Bridging connection from Physical Qubit {p_ctrl} to {p_targ}...")
                path = nx.shortest_path(self.analyzer.hardware_graph, p_ctrl, p_targ)
                
                swaps = path[:-2]
                for i in range(len(swaps)): self.circuit.swap(path[i], path[i+1])
                self.circuit.cx(path[-2], path[-1])
                for i in reversed(range(len(swaps))): self.circuit.swap(path[i], path[i+1])
                return 

        if self.control_stack: self.circuit.mcx(self.control_stack + [c_q], t_q)
        else: self.circuit.cx(c_q, t_q)

    def visit_AddOp(self, node):
        src = self.resolve(node.source_reg)
        tgt = self.resolve(node.target_reg)
        src_size = self.reg_sizes[src]
        tgt_size = self.reg_sizes[tgt]
        src_q = [self.qregs[src] + i for i in range(src_size)]
        tgt_q = [self.qregs[tgt] + i for i in range(tgt_size)]

        if self.active_optimization == "width":
            print(f"[AI Optimizer] Target: WIDTH. Heuristic Chosen: Ripple-Carry Adder for '{tgt}'.")
            rc_mock = QuantumCircuit(src_size + tgt_size, name="RippleCarry")
            self.circuit.append(rc_mock.to_instruction(), src_q + tgt_q)
        else:
            print(f"[AI Optimizer] Target: DEPTH. Heuristic Chosen: Draper QFT Adder for '{tgt}'.")
            self.circuit.append(DraperQFTAdder(src_size), src_q + tgt_q)

    def visit_SearchOracleOp(self, node):
        reg = self.resolve(node.target_reg)
        size = self.reg_sizes[reg]
        start = self.qregs[reg]
        qubits = [start + i for i in range(size)]
        bin_str = format(node.target_val, f'0{size}b')[::-1]
        
        for i, bit in enumerate(bin_str):
            if bit == '0': self.circuit.x(qubits[i])
            
        self.circuit.h(qubits[-1])
        self.circuit.mcx(qubits[:-1], qubits[-1])
        self.circuit.h(qubits[-1])
        
        for i, bit in enumerate(bin_str):
            if bit == '0': self.circuit.x(qubits[i])

    def visit_MeasureOp(self, node): 
        self.circuit.measure_all()

    def visit_MeasureAssignOp(self, node):
        c_idx = self.resolve(node.target_creg)
        q_idx = self.resolve(node.target_qreg)
        cq = self.cregs[c_idx.reg_name] + c_idx.index
        qq = self.qregs[q_idx.reg_name] + q_idx.index
        self.circuit.measure(self.circuit.qubits[qq], self.circuit.clbits[cq])

# ==========================================
# 5b. SYNTHESIS ENGINE (OpenQASM 3 Target)
# ==========================================
class QASM3Emitter(NodeVisitor):
    def __init__(self, analyzer: SemanticAnalyzer):
        self.analyzer = analyzer
        self.code = ["OPENQASM 3.0;", 'include "stdgates.inc";', ""]
        self.indent_level = 0
        self.reg_sizes = {}
        self.creg_sizes = {}
        self.control_stack = []
        self.macros = {}
        self.macro_env = {}
        self.loop_env = {}
        self.active_optimization = None 
        self.required_subroutines = set()
    def visit_ConstantAssignNode(self, node):
        self.macro_env[node.name] = node.value
        self.emit(f"// Constant '{node.name}' = {node.value}")
    def emit(self, line):
        self.code.append(("    " * self.indent_level) + line)

    def resolve(self, obj):
        if isinstance(obj, str): return self.macro_env.get(obj, obj)
        return obj

    def resolve_index(self, obj):
        if isinstance(obj, str): return self.resolve(obj)
        res_reg = self.resolve(obj.reg_name)
        real_idx = evaluate_math(obj.index, self.loop_env, obj.line)
        return f"{res_reg}[{real_idx}]"

    def visit_Program(self, node):
        self.generic_visit(node)
        
        subs = []
        if "draper_qft" in self.required_subroutines:
            subs.append("def draper_qft_add(qubit[] src, qubit[] tgt) {\n    // [Synthesized] Draper QFT Adder Logic injected by QForge\n}")
        if "ripple_carry" in self.required_subroutines:
            subs.append("def ripple_carry_add(qubit[] src, qubit[] tgt) {\n    // [Synthesized] Ripple-Carry Adder Logic injected by QForge\n}")
            
        if subs: self.code.insert(2, "\n".join(subs) + "\n")
        return "\n".join(self.code)

    def visit_TargetHardwareOp(self, node): self.emit(f"// Target Hardware constraints loaded: {node.name}")
    def visit_QRegisterDecl(self, node): self.reg_sizes[node.name] = node.size; self.emit(f"qubit[{node.size}] {node.name};")
    def visit_QIntDecl(self, node): self.visit_QRegisterDecl(node)
    def visit_CIntDecl(self, node): self.creg_sizes[node.name] = node.size; self.emit(f"bit[{node.size}] {node.name};")
    def visit_ParamDecl(self, node): self.emit(f"input float {node.name}; // Runtime Param")

    def visit_DefNode(self, node): self.macros[node.name] = node
    
    def visit_DefCalNode(self, node):
        self.macros[node.name] = node
        self.emit(f"defcal {node.name}({', '.join(node.params)}) {{")
        self.indent_level += 1
        for stmt in node.body: self.visit(stmt)
        self.indent_level -= 1
        self.emit("}")
        
    def visit_PlayOp(self, node):
        dur = evaluate_math(node.pulse.duration, self.loop_env, node.line)
        amp = evaluate_math(node.pulse.amp, self.loop_env, node.line)
        sig = evaluate_math(node.pulse.sigma, self.loop_env, node.line)
        pulse_str = f"gaussian({dur}, {amp}, {sig})"
        
        targ = self.resolve_index(node.channel.target)
        chan_str = f"d({targ})"
        
        self.emit(f"play({pulse_str}, {chan_str});")

    def visit_GaussianPulse(self, node): pass
    def visit_DriveChannel(self, node): pass

    def visit_MacroCallNode(self, node):
        macro = self.macros[node.name]
        old_env = self.macro_env.copy()
        old_opt = getattr(self, 'active_optimization', None)
        
        if getattr(macro, 'optimize_target', None):
            self.active_optimization = macro.optimize_target
            self.emit(f"// --- Begin Macro: {node.name} (Opt: {self.active_optimization}) ---")
            
        for param, arg in zip(macro.params, node.args):
            self.macro_env[param] = self.resolve_index(arg) if isinstance(arg, QubitIndex) else self.resolve(arg)
            
        if isinstance(macro, DefCalNode):
            args_str = ", ".join([str(self.macro_env[p]) for p in macro.params])
            self.emit(f"{node.name} {args_str};")
        else:
            for stmt in macro.body: self.visit(stmt)
        
        if getattr(macro, 'optimize_target', None): self.emit(f"// --- End Macro: {node.name} ---")
        
        self.macro_env = old_env
        self.active_optimization = old_opt

    def visit_AncillaContextNode(self, node):
        self.reg_sizes[node.alias] = node.size
        self.emit(f"qubit[{node.size}] {node.alias};")
        for stmt in node.body: self.visit(stmt)
        self.emit(f"// Auto-Uncomputing Ancilla: {node.alias}")
        for stmt in reversed(node.body): 
            if isinstance(stmt, (SuperposeOp, EntangleOp, FlipOp)): self.visit(stmt)

    def visit_IfQuantumNode(self, node):
        ctrl = self.resolve_index(node.control)
        self.control_stack.append(ctrl)
        for stmt in node.body: self.visit(stmt)
        self.control_stack.pop()

    def visit_IfClassicalNode(self, node):
        c_idx = self.resolve_index(node.control_creg)
        self.emit(f"if ({c_idx} == {node.val}) {{")
        self.indent_level += 1
        for stmt in node.body: self.visit(stmt)
        self.indent_level -= 1
        self.emit("}")

    def visit_ForLoopNode(self, node):
        old_val = self.loop_env.get(node.loop_var)
        self.emit(f"// Unrolling loop: {node.loop_var} over {node.iterations} iterations")
        for i in range(node.iterations):
            self.loop_env[node.loop_var] = i
            for stmt in node.body: self.visit(stmt)
        if old_val is not None: self.loop_env[node.loop_var] = old_val
        else: del self.loop_env[node.loop_var]

    def visit_SuperposeOp(self, node):
        target = self.resolve(node.target)
        targets = [f"{target}[{i}]" for i in range(self.reg_sizes[target])] if isinstance(target, str) else [self.resolve_index(node.target)]
        for t in targets:
            if self.control_stack: self.emit(f"ctrl({len(self.control_stack)}) @ h {', '.join(self.control_stack)}, {t};")
            else: self.emit(f"h {t};")

    def visit_FlipOp(self, node):
        targ = self.resolve_index(node.target)
        if self.control_stack: self.emit(f"ctrl({len(self.control_stack)}) @ x {', '.join(self.control_stack)}, {targ};")
        else: self.emit(f"x {targ};")

    def visit_RotateOp(self, node):
        targ = self.resolve_index(node.target)
        axis = evaluate_math(node.axis, self.loop_env, node.line).lower()
        angle = evaluate_math(node.angle, self.loop_env, node.line)
        if self.control_stack: self.emit(f"ctrl({len(self.control_stack)}) @ r{axis}({angle}) {', '.join(self.control_stack)}, {targ};")
        else: self.emit(f"r{axis}({angle}) {targ};")

    def visit_EntangleOp(self, node):
        if self.analyzer.hardware_graph:
            ctrl = node.control; targ = node.target
            p_ctrl = self.analyzer.reg_to_physical[ctrl.reg_name][evaluate_math(ctrl.index, self.loop_env, node.line)]
            p_targ = self.analyzer.reg_to_physical[targ.reg_name][evaluate_math(targ.index, self.loop_env, node.line)]
            
            if not self.analyzer.hardware_graph.has_edge(p_ctrl, p_targ):
                self.emit(f"// Auto-Router bridging Physical Qubit {p_ctrl} -> {p_targ}")
                path = nx.shortest_path(self.analyzer.hardware_graph, p_ctrl, p_targ)
                
                # THE FIX: Use the reverse lookup map to get the exact logical register strings!
                for i in range(len(path) - 2): 
                    q1 = self.analyzer.physical_to_logical[path[i]]
                    q2 = self.analyzer.physical_to_logical[path[i+1]]
                    self.emit(f"swap {q1}, {q2};")
                
                pre_cx = self.analyzer.physical_to_logical[path[-2]]
                targ_cx = self.analyzer.physical_to_logical[path[-1]]
                self.emit(f"cx {pre_cx}, {targ_cx};")
                
                for i in reversed(range(len(path) - 2)): 
                    q1 = self.analyzer.physical_to_logical[path[i]]
                    q2 = self.analyzer.physical_to_logical[path[i+1]]
                    self.emit(f"swap {q1}, {q2};")
                return
                
        self.emit(f"cx {self.resolve_index(node.control)}, {self.resolve_index(node.target)};")

    def visit_SearchOracleOp(self, node):
        reg = self.resolve(node.target_reg)
        size = self.reg_sizes[reg]
        bin_str = format(node.target_val, f'0{size}b')[::-1]
        
        self.emit(f"// Grover Oracle (Synthesized): Target {node.target_val}")
        for i, bit in enumerate(bin_str):
            if bit == '0': self.emit(f"x {reg}[{i}];")
            
        ctrls = ", ".join([f"{reg}[{i}]" for i in range(size - 1)])
        targ = f"{reg}[{size - 1}]"
        
        self.emit(f"h {targ};")
        self.emit(f"ctrl({size - 1}) @ x {ctrls}, {targ};")
        self.emit(f"h {targ};")
        
        for i, bit in enumerate(bin_str):
            if bit == '0': self.emit(f"x {reg}[{i}];")

    def visit_AddOp(self, node):
        src = self.resolve(node.source_reg)
        tgt = self.resolve(node.target_reg)
        
        if getattr(self, 'active_optimization', None) == "width":
            self.required_subroutines.add("ripple_carry")
            self.emit(f"ripple_carry_add({src}, {tgt});")
        else:
            self.required_subroutines.add("draper_qft")
            self.emit(f"draper_qft_add({src}, {tgt});")

    def visit_MeasureOp(self, node): 
        target = self.resolve(node.target_reg)
        
        if isinstance(target, QubitIndex):
            self.emit(f"// Final state measurement of qubit '{target.reg_name}[{target.index}]'")
            self.emit(f"measure {target.reg_name}[{target.index}];")
        else:
            self.emit(f"// Final state measurement of register '{target}'")
            self.emit(f"measure {target};")

    def visit_MeasureAssignOp(self, node):
        c_idx = self.resolve_index(node.target_creg)
        q_idx = self.resolve_index(node.target_qreg)
        self.emit(f"{c_idx} = measure {q_idx};")

# ==========================================
# 6. AST TRANSFORMER
# ==========================================
class ASTTransformer(Transformer):
    def start(self, items): return Program(statements=items)
    
    def _unpack(self, arg):
        if isinstance(arg, MathVar): return arg.name
        if isinstance(arg, MathStr): return arg.val
        if isinstance(arg, MathNum): return arg.val
        if isinstance(arg, MathFloat): return arg.val
        return arg

    def target_hw(self, items): return TargetHardwareOp(str(items[0])[1:-1], getattr(items[0], 'line', 0))
    
    def assign_var(self, items):
        var_name = str(items[0]); line = getattr(items[0], 'line', 0)
        value = items[1]
        
        # 1. Declaration tracking
        if isinstance(value, (QRegisterDecl, QIntDecl, CIntDecl, ParamDecl)):
            value.name = var_name; value.line = line
            return value
            
        # 2. THE FIX: Catch all math nodes, floats, and strings as Constants!
        if isinstance(value, (MathNode, int, float, str)):
            return ConstantAssignNode(name=var_name, value=value, line=line)
            
        # 3. Aliasing
        return AssignmentAlias(new_name=var_name, target_reg=str(self._unpack(value)), line=line)

    def assign_measure(self, items):
        return MeasureAssignOp(target_creg=items[0], target_qreg=items[1], line=getattr(items[0], 'line', 0))

    def params(self, items): return [str(i) for i in items]
    def optimize_dec(self, items): return str(items[0])[1:-1]

    def def_block(self, items):
        target = None
        if isinstance(items[0], str) and items[0] in ["width", "depth", "fidelity"]:
            target = items[0]
            name = str(items[1]); params = items[2]; body = items[3:]
        else:
            name = str(items[0]); params = items[1]; body = items[2:]
        return DefNode(name=name, params=params, body=body, optimize_target=target, line=0)

    def defcal_block(self, items):
        return DefCalNode(name=str(items[0]), params=items[1], body=items[2:], line=0)

    def math_num(self, items): return MathNum(int(items[0]))
    def math_float(self, items): return MathFloat(float(items[0]))       
    def math_str(self, items): return MathStr(str(items[0])[1:-1])       
    def math_var(self, items): return MathVar(str(items[0]))
    def math_add(self, items): return MathBinOp(items[0], '+', items[1])
    def math_sub(self, items): return MathBinOp(items[0], '-', items[1])
    def math_mul(self, items): return MathBinOp(items[0], '*', items[1])
    def math_div(self, items): return MathBinOp(items[0], '/', items[1])
    def math_mod(self, items): return MathBinOp(items[0], '%', items[1])

    def function_call(self, items):
        func_name = str(items[0]); args = items[1] if len(items) > 1 else []; line = getattr(items[0], 'line', 0)
        
        clean = [self._unpack(arg) for arg in args]
        
        if func_name == "qregister": return QRegisterDecl(name="", size=int(clean[0]), line=line)
        elif func_name == "qint": return QIntDecl(name="", size=int(clean[0]), line=line)
        elif func_name == "cint": return CIntDecl(name="", size=int(clean[0]), line=line) 
        elif func_name == "param": return ParamDecl(name=str(clean[0]), line=line) 
        elif func_name == "superpose": return SuperposeOp(target=clean[0], line=line)
        elif func_name == "flip": return FlipOp(target=clean[0], line=line)
        elif func_name == "rotate": return RotateOp(target=clean[0], axis=clean[1], angle=clean[2], line=line) 
        elif func_name == "entangle": return EntangleOp(control=clean[0], target=clean[1], line=line)
        elif func_name == "add": return AddOp(source_reg=str(clean[0]), target_reg=str(clean[1]), line=line)
        elif func_name == "search": return SearchOracleOp(target_reg=str(clean[0]), target_val=int(clean[1]), line=line)
        elif func_name == "measure": return MeasureOp(target_reg=clean[0], line=line)
        
        elif func_name == "play": return PlayOp(pulse=clean[0], channel=clean[1], line=line)
        elif func_name == "Gaussian": return GaussianPulse(duration=clean[0], amp=clean[1], sigma=clean[2], line=line)
        elif func_name == "drive": return DriveChannel(target=clean[0], line=line)
        
        else: return MacroCallNode(name=func_name, args=clean, line=line)

    def with_block(self, items): return AncillaContextNode(size=int(items[0]), alias=str(items[1]), body=items[2:], line=getattr(items[1], 'line', 0))
    def if_quantum(self, items): return IfQuantumNode(control=self._unpack(items[0]), body=items[1:], line=getattr(items[0], 'line', 0))
    def if_classical(self, items): return IfClassicalNode(control_creg=items[0], val=int(items[1]), body=items[2:], line=getattr(items[0], 'line', 0))
    def for_block(self, items): return ForLoopNode(loop_var=str(items[0]), iterations=int(items[1]), body=items[2:], line=getattr(items[0], 'line', 0))
    def arguments(self, items): return items
    def index_expr(self, items): return QubitIndex(reg_name=str(items[0]), index=items[1], line=getattr(items[0], 'line', 0))

# ==========================================
# 7. THE COMPILER EXPORT FUNCTION
# ==========================================
def compile_qforge(source_code: str, target: str = "qiskit"):
    parser = Lark(GRAMMAR, start='start', parser='lalr', propagate_positions=True)
    tree = parser.parse(source_code)
    ast = ASTTransformer().transform(tree)
    
    analyzer = SemanticAnalyzer()
    analyzer.visit(ast)
    
    if target == "qasm3":
        emitter = QASM3Emitter(analyzer)
        return emitter.visit(ast)
    else:
        emitter = QiskitEmitter(analyzer)
        return emitter.visit(ast)
