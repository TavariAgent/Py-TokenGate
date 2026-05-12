# -*- coding: utf-8 -*-
# code_inspector.py
"""
Pre-execution code inspection for allocation and confidence heuristics.

This module analyzes function bytecode and code-object metadata before
execution to estimate relative complexity, likely resource pressure, and
initial allocation guidance.

The results are heuristic. They are intended to improve first-pass budget
selection and safety decisions before runtime measurements are available.
"""

import dis
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, Any, List


class ComplexityLevel(Enum):
    """Discrete complexity bands derived from heuristic bytecode analysis."""
    TRIVIAL = 1  # < 10 score: Simple getters, basic math
    SIMPLE = 2  # 10-30: Standard business logic
    MODERATE = 3  # 30-60: Multiple branches, some loops
    COMPLEX = 4  # 60-100: Heavy computation, nested loops
    EXTREME = 5  # > 100: Matrix ops, recursive algorithms


@dataclass
class CodeMetrics:
    """Structured result of one code-object analysis pass."""
    func_name: str
    bytecode_length: int
    stack_size: int
    local_vars: int
    arg_count: int
    const_count: int
    const_footprint: int
    external_calls: List[str]
    loop_count: int
    branch_count: int
    complexity_score: float
    complexity_level: ComplexityLevel
    confidence: float

    def to_dict(self) -> Dict[str, Any]:
        """Return a serialization-friendly dictionary representation."""
        return {
            'func_name': self.func_name,
            'bytecode_length': self.bytecode_length,
            'stack_size': self.stack_size,
            'local_vars': self.local_vars,
            'arg_count': self.arg_count,
            'const_count': self.const_count,
            'const_footprint': self.const_footprint,
            'external_calls': self.external_calls,
            'loop_count': self.loop_count,
            'branch_count': self.branch_count,
            'complexity_score': self.complexity_score,
            'complexity_level': self.complexity_level.name,
            'confidence': self.confidence
        }


class CodeInspector:
    """Static bytecode analyzer for pre-execution resource heuristics.

    Inspects code objects to derive complexity indicators, confidence scores,
    and initial allocation recommendations before runtime profiling exists.
    """

    # Weight factors for complexity calculation
    WEIGHTS = {
        'bytecode': 0.3,  # Bytecode length impact
        'stack': 0.2,  # Stack depth impact
        'constants': 0.15,  # Constant footprint impact
        'calls': 0.15,  # External call impact
        'loops': 0.1,  # Loop impact
        'branches': 0.1  # Branch impact
    }

    # Confidence modifiers
    CONFIDENCE_BASE = 85.0  # Start at 85% confidence
    CONFIDENCE_DECAY = 0.3  # Lose 0.3% per complexity point above 50

    @staticmethod
    def analyze(func: Callable) -> CodeMetrics:
        """
        Analyze a callable's code object and derive heuristic complexity metrics.

        Args:
            func: Callable to inspect. Must expose a ``__code__`` attribute.

        Returns:
            CodeMetrics containing bytecode, control-flow, and confidence data.
        """
        from .tg_print import tg_print
        if not hasattr(func, '__code__'):
            raise ValueError(f"Function {func} has no __code__ attribute")

        code = func.__code__  # type: ignore[attr-defined]

        # Extract basic metrics
        func_name = code.co_name
        bytecode_length = len(code.co_code)
        stack_size = code.co_stacksize
        local_vars = code.co_nlocals
        arg_count = code.co_argcount

        # Analyze constants
        const_count = len(code.co_consts)
        const_footprint = CodeInspector._calculate_const_footprint(code.co_consts)

        # Analyze external calls
        external_calls = CodeInspector._extract_external_calls(code)

        expensive = {'numpy', 'pandas', 'scipy', 'requests', 'urllib', 'exec', 'eval', 'compile'}
        flagged = [c for c in external_calls if any(e in c for e in expensive)]
        if flagged:
            tg_print('overflow',f'{func_name} references expensive externals: {flagged}', level='warn')

        # Analyze control flow
        loop_count = CodeInspector._estimate_loops(code)
        branch_count = CodeInspector._estimate_branches(code)

        # Calculate complexity score
        complexity_score = CodeInspector._calculate_complexity(
            bytecode_length=bytecode_length,
            stack_size=stack_size,
            const_footprint=const_footprint,
            external_calls_count=len(external_calls),
            loop_count=loop_count,
            branch_count=branch_count
        )

        # Classify complexity
        complexity_level = CodeInspector._classify_complexity(complexity_score)

        if complexity_level == ComplexityLevel.EXTREME:
            tg_print('overflow',f'{func_name} '
                                f'classified EXTREME (score={complexity_score:.1f}) '
                                f'allocation multiplier 4x', level='warn')

        # Calculate confidence
        confidence = CodeInspector._calculate_confidence(complexity_score)

        tg_print('overflow', f'Inspected {func_name}  '
                        f'complexity={complexity_level.name}  '
                        f'score={complexity_score:.1f}  '
                        f'confidence={confidence}%', level='debug')

        return CodeMetrics(
            func_name=func_name,
            bytecode_length=bytecode_length,
            stack_size=stack_size,
            local_vars=local_vars,
            arg_count=arg_count,
            const_count=const_count,
            const_footprint=const_footprint,
            external_calls=external_calls,
            loop_count=loop_count,
            branch_count=branch_count,
            complexity_score=complexity_score,
            complexity_level=complexity_level,
            confidence=confidence
        )

    @staticmethod
    def _calculate_const_footprint(consts: tuple) -> int:
        """Estimate the memory footprint of constants referenced by a code object."""
        footprint = 0

        for const in consts:
            if const is None:
                continue

            if isinstance(const, (str, bytes)):
                footprint += len(const)
            elif isinstance(const, (list, tuple)):
                # Recursively estimate collection size
                footprint += sys.getsizeof(const)
            elif isinstance(const, dict):
                footprint += sys.getsizeof(const)
            elif isinstance(const, (int, float)):
                footprint += sys.getsizeof(const)
            else:
                # Generic object
                try:
                    footprint += sys.getsizeof(const)
                except:
                    footprint += 64  # Default estimate

        return footprint

    @staticmethod
    def _extract_external_calls(code) -> List[str]:
        """Collect referenced external names from the code object.

        This is a name-based heuristic used to flag likely library, I/O, or
        dynamic-execution behavior.
        """
        external_calls = []
        names = set(code.co_names)

        for name in names:
            # Track all names but flag expensive ones
            external_calls.append(name)

        return sorted(external_calls)

    @staticmethod
    def _estimate_loops(code) -> int:
        """Estimate loop presence from bytecode instruction patterns."""
        loop_count = 0
        instructions = list(dis.get_instructions(code))

        for instr in instructions:
            # FOR_ITER indicates a for loop
            if instr.opname == 'FOR_ITER':
                loop_count += 1
            # JUMP_BACKWARD can indicate while loops
            elif instr.opname in ('JUMP_BACKWARD', 'JUMP_ABSOLUTE'):
                # Be conservative - not all jumps are loops
                # Only count backward jumps as likely loops
                if hasattr(instr, 'argval') and isinstance(instr.argval, int):
                    if instr.argval < instr.offset:
                        loop_count += 0.5  # Weight lower since might not be loop

        return int(loop_count)

    @staticmethod
    def _estimate_branches(code) -> int:
        """Estimate conditional branch count from jump instructions."""
        branch_count = 0
        instructions = list(dis.get_instructions(code))

        for instr in instructions:
            if 'POP_JUMP' in instr.opname or 'JUMP_IF' in instr.opname:
                branch_count += 1

        return branch_count

    @staticmethod
    def _calculate_complexity(
            bytecode_length: int,
            stack_size: int,
            const_footprint: int,
            external_calls_count: int,
            loop_count: int,
            branch_count: int
    ) -> float:
        """
        Combine normalized static indicators into a heuristic complexity score.

        Higher scores suggest greater structural complexity and potentially
        higher initial resource pressure.
        """
        # Normalize metrics to 0-1 scale
        # These thresholds are heuristic - adjust based on real data
        norm_bytecode = min(bytecode_length / 500, 1.0)
        norm_stack = min(stack_size / 20, 1.0)
        norm_constants = min(const_footprint / 10000, 1.0)
        norm_calls = min(external_calls_count / 15, 1.0)
        norm_loops = min(loop_count / 5, 1.0)
        norm_branches = min(branch_count / 10, 1.0)

        # Apply weights
        w = CodeInspector.WEIGHTS
        weighted_sum = (
                norm_bytecode * w['bytecode'] +
                norm_stack * w['stack'] +
                norm_constants * w['constants'] +
                norm_calls * w['calls'] +
                norm_loops * w['loops'] +
                norm_branches * w['branches']
        )

        # Scale to 0-200+ range (most functions will be 0-100)
        complexity = weighted_sum * 200

        return round(complexity, 2)

    @staticmethod
    def _classify_complexity(score: float) -> ComplexityLevel:
        """Map a complexity score to its configured complexity band."""
        if score < 10:
            return ComplexityLevel.TRIVIAL
        elif score < 30:
            return ComplexityLevel.SIMPLE
        elif score < 60:
            return ComplexityLevel.MODERATE
        elif score < 100:
            return ComplexityLevel.COMPLEX
        else:
            return ComplexityLevel.EXTREME

    @staticmethod
    def _calculate_confidence(complexity_score: float) -> float:
        """
        Convert complexity score into a heuristic prediction-confidence value.

        Simpler functions retain higher confidence; more structurally complex
        functions reduce confidence.
        """
        base = CodeInspector.CONFIDENCE_BASE
        decay = CodeInspector.CONFIDENCE_DECAY

        if complexity_score <= 50:
            # Simple functions maintain high confidence
            confidence = base
        else:
            # Complex functions lose confidence
            excess_complexity = complexity_score - 50
            confidence = base - (excess_complexity * decay)

        # Clamp to a reasonable range
        confidence = max(20.0, min(95.0, confidence))

        return round(confidence, 1)

    @staticmethod
    def predict_initial_allocation(metrics: CodeMetrics, base_budget_mb: int = 50) -> Dict[str, Any]:
        """
        Derive an initial memory and timeout recommendation from analyzed metrics.

        Args:
            metrics: CodeMetrics returned by ``analyze()``.
            base_budget_mb: Baseline memory budget used for scaling.

        Returns:
            Dictionary containing recommended memory, timeout, confidence,
            reasoning text, and the underlying complexity score.
        """
        # Complexity-based multipliers
        multipliers = {
            ComplexityLevel.TRIVIAL: 0.5,
            ComplexityLevel.SIMPLE: 1.0,
            ComplexityLevel.MODERATE: 1.5,
            ComplexityLevel.COMPLEX: 2.5,
            ComplexityLevel.EXTREME: 4.0
        }

        multiplier = multipliers[metrics.complexity_level]

        # Calculate allocations
        memory_mb = int(base_budget_mb * multiplier)

        # Timeout based on complexity
        timeout_seconds = 10 + (metrics.complexity_score * 0.5)
        timeout_seconds = min(timeout_seconds, 300)  # Cap at 5 minutes

        # Build reasoning
        reasoning = f"Complexity: {metrics.complexity_level.name} (score: {metrics.complexity_score:.1f}), "
        reasoning += f"Confidence: {metrics.confidence}%"

        if metrics.loop_count > 0:
            reasoning += f", {metrics.loop_count} loop(s)"
        if len(metrics.external_calls) > 5:
            reasoning += f", {len(metrics.external_calls)} external calls"

        return {
            'memory_mb': memory_mb,
            'timeout_seconds': timeout_seconds,
            'confidence': metrics.confidence,
            'reasoning': reasoning,
            'complexity_score': metrics.complexity_score
        }