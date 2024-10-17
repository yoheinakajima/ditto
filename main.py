import os
import sys
import json
import importlib
import traceback
from flask import Flask, Blueprint, request, send_from_directory, render_template_string, jsonify
from threading import Thread
from time import sleep

# Correctly import the completion function from LiteLLM
from litellm import completion, supports_function_calling

# Configuration
MODEL_NAME = os.environ.get('LITELLM_MODEL', 'gpt-4o')  # Default model; can be swapped easily

# Initialize Flask app
app = Flask(__name__)

LOG_FILE = "flask_app_builder_log.json"

# Directory paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, 'templates')
STATIC_DIR = os.path.join(BASE_DIR, 'static')
ROUTES_DIR = os.path.join(BASE_DIR, 'routes')

# Initialize progress tracking
progress = {
    "status": "idle",
    "iteration": 0,
    "max_iterations": 50,
    "output": "",
    "completed": False
}

# Ensure directories exist and create __init__.py in routes
def create_directory(path):
    if not os.path.exists(path):
        os.makedirs(path)
        # If creating the routes directory, add __init__.py
        if path == ROUTES_DIR:
            create_file(os.path.join(ROUTES_DIR, '__init__.py'), '')
        return f"Created directory: {path}"
    return f"Directory already exists: {path}"

def create_file(path, content):
    try:
        with open(path, 'x') as f:
            f.write(content)
        return f"Created file: {path}"
    except FileExistsError:
        with open(path, 'w') as f:
            f.write(content)
        return f"Updated file: {path}"
    except Exception as e:
        return f"Error creating/updating file {path}: {e}"

def update_file(path, content):
    try:
        with open(path, 'w') as f:
            f.write(content)
        return f"Updated file: {path}"
    except Exception as e:
        return f"Error updating file {path}: {e}"

def fetch_code(file_path):
    try:
        with open(file_path, 'r') as f:
            code = f.read()
        return code
    except Exception as e:
        return f"Error fetching code from {file_path}: {e}"

def load_routes():
    try:
        if BASE_DIR not in sys.path:
            sys.path.append(BASE_DIR)
        for filename in os.listdir(ROUTES_DIR):
            if filename.endswith('.py') and filename != '__init__.py':
                module_name = filename[:-3]
                module_path = f'routes.{module_name}'
                try:
                    if module_path in sys.modules:
                        importlib.reload(sys.modules[module_path])
                    else:
                        importlib.import_module(module_path)
                    module = sys.modules.get(module_path)
                    if module:
                        # Find all blueprint objects in the module
                        for attr_name in dir(module):
                            attr = getattr(module, attr_name)
                            if isinstance(attr, Blueprint):
                                app.register_blueprint(attr)
                except Exception as e:
                    print(f"Error importing module {module_path}: {e}")
                    continue
        print("Routes loaded successfully.")
        return "Routes loaded successfully."
    except Exception as e:
        print(f"Error in load_routes: {e}")
        return f"Error loading routes: {e}"

def task_completed():
    progress["status"] = "completed"
    progress["completed"] = True
    return "Task marked as completed."

# Initialize necessary directories
create_directory(TEMPLATES_DIR)
create_directory(STATIC_DIR)
create_directory(ROUTES_DIR)  # This will also create __init__.py in routes

# Load routes once at initiation
load_routes()

# Function to log history to file
def log_to_file(history_dict):
    try:
        with open(LOG_FILE, 'w') as log_file:
            json.dump(history_dict, log_file, indent=4)
    except Exception as e:
        pass  # Silent fail

# Default route to serve generated index.html or render a form
@app.route('/', methods=['GET', 'POST'])
def home():
    index_file = os.path.join(TEMPLATES_DIR, 'index.html')
    if os.path.exists(index_file):
        return send_from_directory(TEMPLATES_DIR, 'index.html')
    else:
        if request.method == 'POST':
            user_input = request.form.get('user_input')
            # Run the main loop with the user's input in a separate thread
            progress["status"] = "running"
            progress["iteration"] = 0
            progress["output"] = ""
            progress["completed"] = False
            thread = Thread(target=run_main_loop, args=(user_input,))
            thread.start()
            return render_template_string('''
                <h1>Progress</h1>
                <pre id="progress">{{ progress_output }}</pre>
                <script>
                    setInterval(function() {
                        fetch('/progress')
                        .then(response => response.json())
                        .then(data => {
                            document.getElementById('progress').innerHTML = data.output;
                            if (data.completed) {
                                document.getElementById('refresh-btn').style.display = 'block';
                            }
                        });
                    }, 2000);
                </script>
                <button id="refresh-btn" style="display:none;" onclick="location.reload();">Refresh Page</button>
            ''', progress_output=progress["output"])
        else:
            return render_template_string('''
                <h1>Flask App Builder</h1>
                <form method="post">
                    <label for="user_input">Describe the Flask app you want to create:</label><br>
                    <textarea id="user_input" name="user_input" style="width:100%; height:150px; padding:10px; border:1px solid #ccc; border-radius:4px; font-size:16px; resize:vertical;"></textarea><br><br>
                    <input type="submit" value="Submit">
                </form>
            ''')

# Route to provide progress updates
@app.route('/progress')
def get_progress():
    return jsonify(progress)

# Available functions for the LLM
available_functions = {
    "create_directory": create_directory,
    "create_file": create_file,
    "update_file": update_file,
    "fetch_code": fetch_code,
    "task_completed": task_completed
}

# Define the tools for function calling
tools = [
    {
        "type": "function",
        "function": {
            "name": "create_directory",
            "description": "Creates a new directory at the specified path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The directory path to create."
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Creates or updates a file at the specified path with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file path to create or update."
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write into the file."
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_file",
            "description": "Updates an existing file at the specified path with the new content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file path to update."
                    },
                    "content": {
                        "type": "string",
                        "description": "The new content to write into the file."
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_code",
            "description": "Retrieves the code from the specified file path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The file path to fetch the code from."
                    }
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_completed",
            "description": "Indicates that the assistant has completed the task.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }
]

def run_main_loop(user_input):
    # Reset the history_dict for each run
    history_dict = {
        "iterations": []
    }

    if not supports_function_calling(MODEL_NAME):
        progress["status"] = "error"
        progress["output"] = "Model does not support function calling."
        progress["completed"] = True
        return "Model does not support function calling."

    max_iterations = progress["max_iterations"]  # Prevent infinite loops
    iteration = 0

    # Updated messages array with enhanced prompt
    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert Flask developer tasked with building a complete, production-ready Flask application based on the user's description. "
                "Before coding, carefully plan out all the files, routes, templates, and static assets needed. "
                "Follow these steps:\n"
                "1. **Understand the Requirements**: Analyze the user's input to fully understand the application's functionality and features.\n"
                "2. **Plan the Application Structure**: List all the routes, templates, and static files that need to be created. Consider how they interact.\n"
                "3. **Implement Step by Step**: For each component, use the provided tools to create directories, files, and write code. Ensure each step is thoroughly completed before moving on.\n"
                "4. **Review and Refine**: Use `fetch_code` to review the code you've written. Update files if necessary using `update_file`.\n"
                "5. **Ensure Completeness**: Do not leave any placeholders or incomplete code. All functions, routes, and templates must be fully implemented and ready for production.\n"
                "6. **Do Not Modify `main.py`**: Focus only on the `templates/`, `static/`, and `routes/` directories.\n"
                "7. **Finalize**: Once everything is complete and thoroughly tested, call `task_completed()` to finish.\n\n"
                "Constraints and Notes:\n"
                "- The application files must be structured within the predefined directories: `templates/`, `static/`, and `routes/`.\n"
                "- Routes should be modular and placed inside the `routes/` directory as separate Python files.\n"
                "- The `index.html` served from the `templates/` directory is the entry point of the app. Update it appropriately if additional templates are created.\n"
                "- Do not use placeholders like 'Content goes here'. All code should be complete and functional.\n"
                "- Do not ask the user for additional input; infer any necessary details to complete the application.\n"
                "- Ensure all routes are properly linked and that templates include necessary CSS and JS files.\n"
                "- Handle any errors internally and attempt to resolve them before proceeding.\n\n"
                "Available Tools:\n"
                "- `create_directory(path)`: Create a new directory.\n"
                "- `create_file(path, content)`: Create or overwrite a file with content.\n"
                "- `update_file(path, content)`: Update an existing file with new content.\n"
                "- `fetch_code(file_path)`: Retrieve the code from a file for review.\n"
                "- `task_completed()`: Call this when the application is fully built and ready.\n\n"
                "Remember to think carefully at each step, ensuring the application is complete, functional, and meets the user's requirements."
            )
        },
        {"role": "user", "content": user_input},
        {"role": "system", "content": f"History:\n{json.dumps(history_dict, indent=2)}"}
    ]

    output = ""

    while iteration < max_iterations:
        progress["iteration"] = iteration + 1
        # Create a new iteration dictionary for each loop
        current_iteration = {
            "iteration": iteration + 1,  # Start from 1
            "actions": [],
            "llm_responses": [],
            "tool_results": [],
            "errors": []
        }
        history_dict['iterations'].append(current_iteration)

        try:
            response = completion(
                model=MODEL_NAME,
                messages=messages,
                tools=tools,
                tool_choice="auto"
            )

            if not response.choices[0].message:
                error = response.get('error', 'Unknown error')
                current_iteration['errors'].append({'action': 'llm_completion', 'error': error})
                log_to_file(history_dict)
                sleep(5)
                iteration += 1
                continue

            # Extract LLM response and append to current iteration
            response_message = response.choices[0].message
            content = response_message.content or ""
            current_iteration['llm_responses'].append(content)

            # Prepare the output string
            output += f"\n<h2>Iteration {iteration + 1}:</h2>\n"

            tool_calls = response_message.tool_calls

            if tool_calls:
                output += "<strong>Tool Call:</strong>\n<p>" + content + "</p>\n"
                messages.append(response_message)

                for tool_call in tool_calls:
                    function_name = tool_call.function.name
                    function_to_call = available_functions.get(function_name)

                    if not function_to_call:
                        error_message = f"Tool '{function_name}' is not available."
                        current_iteration['errors'].append({
                            'action': f'tool_call_{function_name}',
                            'error': error_message,
                            'traceback': 'No traceback available.'
                        })
                        continue

                    try:
                        function_args = json.loads(tool_call.function.arguments)

                        # Call the tool function and store result
                        function_response = function_to_call(**function_args)

                        # Append the tool result under the current iteration
                        current_iteration['tool_results'].append({
                            'tool': function_name,
                            'result': function_response
                        })

                        # Include tool result in the output
                        output += f"<strong>Tool Result ({function_name}):</strong>\n<p>{function_response}</p>\n"

                        # Add tool call details to the conversation
                        messages.append(
                            {"tool_call_id": tool_call.id, "role": "tool", "name": function_name, "content": function_response}
                        )

                        # Check if the assistant called 'task_completed' to signal completion
                        if function_name == "task_completed":
                            progress["status"] = "completed"
                            progress["completed"] = True
                            output += "\n<h2>COMPLETE</h2>\n"
                            progress["output"] = output
                            log_to_file(history_dict)
                            return output  # Exit the function

                    except Exception as tool_error:
                        error_message = f"Error executing {function_name}: {tool_error}"
                        current_iteration['errors'].append({
                            'action': f'tool_call_{function_name}',
                            'error': error_message,
                            'traceback': traceback.format_exc()
                        })

                # Second response to include the tool call
                second_response = completion(
                    model=MODEL_NAME,
                    messages=messages
                )
                if second_response.choices and second_response.choices[0].message:
                    second_response_message = second_response.choices[0].message
                    content = second_response_message.content or ""
                    current_iteration['llm_responses'].append(content)
                    output += "<strong>LLM Response:</strong>\n<p>" + content + "</p>\n"
                    messages.append(second_response_message)
                else:
                    error = second_response.get('error', 'Unknown error in second LLM response.')
                    current_iteration['errors'].append({'action': 'second_llm_completion', 'error': error})

            else:
                output += "<strong>LLM Response:</strong>\n<p>" + content + "</p>\n"
                messages.append(response_message)

            progress["output"] = output

        except Exception as e:
            error = str(e)
            current_iteration['errors'].append({
                'action': 'main_loop',
                'error': error,
                'traceback': traceback.format_exc()
            })

        iteration += 1
        log_to_file(history_dict)
        sleep(2)

    if iteration >= max_iterations:
        progress["status"] = "completed"

    progress["completed"] = True
    progress["status"] = "completed"

    return output

if __name__ == '__main__':
    # Start the Flask app
    app.run(host='0.0.0.0', port=8080)
