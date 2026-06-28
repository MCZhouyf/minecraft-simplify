import os
import re
import utils as U
from javascript import require


def _skill_loader(skill: str):
    file_path = os.path.abspath(os.path.dirname(__file__))
    file_path = U.f_join(file_path, 'ActionLib', skill + '.js')
    with open(file_path, 'r', encoding='utf-8') as file:
        content = file.read()
    return content


def process_message(message):
    retry = 3
    error = None
    while retry > 0:
        try:
            babel = require("@babel/core")
            babel_generator = require("@babel/generator").default
            code = message
            parsed = babel.parse(code)
            functions = []
            assert len(list(parsed.program.body)) > 0, "No functions found"
            for i, node in enumerate(parsed.program.body):
                if node.type != "FunctionDeclaration":
                    continue
                node_type = (
                    "AsyncFunctionDeclaration"
                    if node["async"]
                    else "FunctionDeclaration"
                )
                functions.append(
                    {
                        "name": node.id.name,
                        "type": node_type,
                        "body": babel_generator(node).code,
                        "params": list(node["params"]),
                    }
                )
            # find the last async function
            main_function = None
            for function in reversed(functions):
                if function["type"] == "AsyncFunctionDeclaration":
                    main_function = function
                    break
            assert (
                    main_function is not None
            ), "No async function found. Your main function must be async."
            assert (
                    len(main_function["params"]) == 1
                    and main_function["params"][0].name == "bot"
            ), f"Main function {main_function['name']} must take a single argument named 'bot'"
            program_code = "\n\n".join(function["body"] for function in functions)
            exec_code = f"await {main_function['name']}(bot);"
            return {
                "program_code": program_code,
                "program_name": main_function["name"],
                "exec_code": exec_code,
            }
        except Exception as e:
            retry -= 1
            error = e
    return f"Error parsing action response (before program execution): {error}"


def _fallback_process_actionlib(message):
    """Parse checked-in ActionLib files when the JS bridge/Babel path is flaky.

    Integration tests execute local action files, not arbitrary LLM output. Those
    files are plain async function declarations, so the whole file can be used as
    program_code once we identify the last async function taking `bot`.
    """
    matches = list(re.finditer(
        r"\basync\s+function\s+([A-Za-z_$][\w$]*)\s*\(\s*bot\s*\)",
        message,
    ))
    if not matches:
        return None
    name = matches[-1].group(1)
    return {
        "program_code": message,
        "program_name": name,
        "exec_code": f"await {name}(bot);",
    }


def load_control_primitives(primitive_names=None):
    file_path = os.path.abspath(os.path.dirname(__file__))
    if primitive_names is None:
        primitive_names = [
            primitives[:-3]
            for primitives in os.listdir(f"{file_path}/control_primitives")
            if primitives.endswith(".js")
        ]
    primitives = [
        U.load_text(f"{file_path}/control_primitives/{primitive_name}.js")
        for primitive_name in primitive_names
    ]
    return primitives


def skill_loader(skill: str):
    source = _skill_loader(skill)
    parsed_result = process_message(source)
    if isinstance(parsed_result, str):
        parsed_result = _fallback_process_actionlib(source)
    if not isinstance(parsed_result, dict):
        raise ValueError(f"failed to parse ActionLib skill {skill!r}")
    return "\n".join(load_control_primitives()) + "\n" + parsed_result["program_code"] + "\n" + \
           parsed_result["exec_code"]

#print(skill_loader('mineCoalOre'))
