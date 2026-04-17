# Setting Up the Environment

---

To set up the environment for TokenGate, follow the steps below:

1. Clone or download the repository to your local machine.
2. Ensure you have Python 3.12 installed on your system. 

*Check your Python version by running:*

```
python --version
```
3. Create a virtual environment to manage dependencies. *(required)*
4. Run the following command to install the required dependencies:
```
pip install -r requirements.txt
```
5. Once the dependencies are installed determine valid defaults for your system:
```python
class OperationsCoordinator: # Under this class are where you find configurations for-
    def __init__( # controlling the workers per-core and parallel executors.
            self, 
            workers_per_core: int = 4, # Controls number of workers and may notgo under 2 workers.
            enable_convergence: bool = True, # Controls dynamic worker counts.
            convergence_verbose: bool = False, # DO NOT ENABLE INSIDE A REPL!!!
            base_memory_budget_mb: int = 45, # This is a control for operation memory (adjustable).
            num_executors: int = 8, # This controls number of parallel executors.
            auto_block_dangerous: bool = False, # Blocks explosive tasks with possibly dangerous inputs.
    ):
```

6.  *Even when using the runner ensure you can handle the concurrent writes, tests are currently limited to 25*.

```
- SLOW (10 writes)
- MODERATE (25 writes)
- FAST (50 writes) Note: Storage must fall within your system's capabilities.
- INSANE (70 writes) <- CAUTION
```

7. Try the runner from a directory above the project root.
```
python -m your_project_root.demo.runner
```

8. Read the proofs for setting up your own tasks and to learn more about the system.