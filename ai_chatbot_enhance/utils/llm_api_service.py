import copy
import json
import os
import requests
import typing
from logging import getLogger
import time
from typing import Callable, Any

from ..utils.ai_logging import ai_response_logging, get_ai_logging_session, api_call_logging
from odoo import _
from odoo.api import Environment
from odoo.exceptions import UserError
from ..utils.llm_providers import check_model_depreciation

_logger = getLogger(__name__)


class Embedding(typing.TypedDict):
    index: int
    embedding: list[float]
    object: str


class EmbeddingResponse(typing.TypedDict):
    object: str
    data: list[Embedding]
    model: str
    usage: dict


class LLMApiService:
    def __init__(self, env: Environment, provider: str = 'openai') -> None:
        self.provider = provider
        base_url = None
        if self.provider == 'openai':
            base_url = "https://api.openai.com/v1"
        elif self.provider == 'google':
            base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
        elif self.provider == 'deepseek':
            base_url = "https://api.deepseek.com/beta"
        elif self.provider == 'qwen':
            base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

        else:
            raise NotImplementedError(f"Unsupported provider: {self.provider}")

        self.base_url = base_url
        self.env = env

    def _request_llm(self, *args, **kwargs):
        model = kwargs.get("llm_model") or args[0]
        check_model_depreciation(self.env, model)

        if self.provider == 'openai':
            return self._request_llm_openai(*args, **kwargs)

        if self.provider == 'google':
            return self._request_llm_google(*args, **kwargs)

        if self.provider == 'deepseek':
            return self._request_llm_deepseek(*args, **kwargs)

        if self.provider == 'qwen':
            return self._request_llm_qwen(*args, **kwargs)

        raise NotImplementedError()

    def request_llm(
            self, llm_model: str, system_prompts: list[str], user_prompts: list[str],
            tools: dict[str, tuple[str, bool, Callable[[dict[str, Any]], Any], dict]] | None = None,
            files: list[dict] | None = None, schema: dict | None = None, temperature: float = 0.2,
            inputs: list[dict] | None = None, web_grounding: bool = False,
    ) -> list[str]:
        check_model_depreciation(self.env, llm_model)
        with ai_response_logging(llm_model):
            return self._request_llm_silent_simple(
                llm_model=llm_model,
                system_prompts=system_prompts,
                user_prompts=user_prompts,
                tools=tools,
                files=files,
                schema=schema,
                temperature=temperature,
                inputs=inputs,
                web_grounding=web_grounding,
            )

    def _request_llm_silent_simple(
            self, llm_model: str, system_prompts: list[str], user_prompts: list[str],
            tools: dict[str, tuple[str, bool, Callable[[dict[str, Any]], Any], dict]] | None = None,
            files: list[dict] | None = None, schema: dict | None = None, temperature: float = 0.2,
            inputs: list[dict] | None = None, web_grounding: bool = False,
    ):

        AI_MAX_SUCCESSIVE_CALLS = 20
        AI_MAX_TOOL_CALLS_PER_CALL = 20

        if tools:
            tools = copy.deepcopy(tools)
            for __, allow_end_message, __, tool_parameter_schema in tools.values():
                if allow_end_message and "__end_message" not in tool_parameter_schema["properties"]:
                    tool_parameter_schema["properties"]["__end_message"] = {
                        "type": "string",
                        "description": "If you are not waiting a result and you are done, write here your last message (it must follow the instructions). If you will do an action after this one, leave it empty.",
                    }
                if "__end_message" in tool_parameter_schema["properties"] and "__end_message" not in tool_parameter_schema["required"]:
                    tool_parameter_schema["required"].append("__end_message")

        inputs = inputs or []

        if self.provider == 'google':
            # OpenAI / Odoo inputs -> Gemini
            inputs = [
                {"role": "user" if i["role"] == "user" else "model", "parts": [{"text": i["content"]}]}
                for i in inputs
            ]

        all_responses = []
        for api_call in range(AI_MAX_SUCCESSIVE_CALLS):
            responses, next_actions, inputs = self._request_llm(
                llm_model,
                system_prompts,
                user_prompts,
                files=files,
                inputs=inputs,
                schema=schema,
                tools=tools,
                temperature=temperature,
                web_grounding=web_grounding,
            )
            all_responses.extend(responses)

            _logger.info(
                "[AI DIAG] Loop #%d | available_tools=%s | tool_calls=%s | text_response=%s",
                api_call,
                list(tools.keys()) if tools else [],
                [(name, args) for name, _cid, args in next_actions],
                responses,
            )

            if not next_actions:
                break

            done = False
            session = get_ai_logging_session()

            if session:
                session["tool_calls"] += min(len(next_actions), AI_MAX_TOOL_CALLS_PER_CALL)

            for i, (tool_name, call_id, arguments) in enumerate(next_actions):
                if i >= AI_MAX_TOOL_CALLS_PER_CALL:
                    _logger.warning("AI: Tool call limit reached, stopping further tool calls")
                    inputs.append(self._build_tool_call_response(call_id, "Error: This tool call isn't processed because of tool call limit, try again"))
                    continue

                if tool_name not in tools:
                    _logger.error("AI: Try to call a forbidden action %s", tool_name)
                    inputs.append(self._build_tool_call_response(call_id, f"Error: unknown tool '{tool_name}'. Try again with the correct tool name."))
                    continue

                has_end_message = "__end_message" in arguments
                end_message = arguments.pop("__end_message", None)
                result, error = tools[tool_name][2](arguments=arguments)

                inputs.append(self._build_tool_call_response(call_id, result))

                if has_end_message and error is None:
                    done = True
                    if end_response := end_message and end_message.strip():
                        all_responses.append(end_response)
                        _logger.info("AI: action terminate early: %s", end_response)
                    else:
                        _logger.info("AI: action terminate early with empty message")

            if session and len(next_actions) > 1:  # Batch of tool calls
                _logger.debug("[AI Tool Summary] Batch #%d completed, %d tool calls", session["current_batch_id"], len(next_actions))

            if done:
                break

        _logger.info("AI: API calls %s", api_call + 1)

        if not all_responses:
            error_msg = "Processing loop ended with no response."
            if api_call + 1 >= AI_MAX_SUCCESSIVE_CALLS:
                error_msg = "Number of successive API calls exceeded, please try again with a more precise request."
            raise ValueError(error_msg)

        return all_responses

    def _request_llm_openai(
        self, llm_model, system_prompts, user_prompts, tools=None,
        files=None, schema=None, temperature=0.2, inputs=(), web_grounding=False
    ):
        user_content = [{"type": "input_text", "text": prompt} for prompt in user_prompts]

        if files:
            def _build_file(idx, file):
                if file["mimetype"] == "text/plain":
                    return {"type": "input_text", "text": file["value"]}

                file_uri = f"data:{file['mimetype']};base64,{file['value']}"
                if file['mimetype'] == 'application/pdf':
                    return {
                        "type": "input_file",
                        "filename": f"file_{idx}.pdf",
                        "file_data": file_uri,
                    }

                assert file["mimetype"].startswith("image/")
                return {"type": "input_image", "image_url": file_uri, "detail": "low"}

            user_content.extend(
                _build_file(idx, file)
                for idx, file in enumerate(files, start=1)
            )

        body = {
            "model": llm_model,
            "input": [
                {
                    "role": "system",
                    "content": [
                        {"type": "input_text", "text": prompt}
                        for prompt in system_prompts
                    ],
                },
                {"role": "user", "content": user_content},
                *inputs,
            ],
            "store": False,
        }
        if llm_model not in ('gpt-5', 'gpt-5-mini'):
            body["temperature"] = temperature

        if schema:
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "json_schema",
                    "schema": schema,
                    "strict": True,
                },
            }

        if tools:
            body["tools"] = self._to_open_ai_tool_schema([{
                "description": tool_description,
                "parameters": tool_parameter_schema,
                "type": "function",
                "name": tool_name,
                "strict": True,
            } for tool_name, (tool_description, __, __, tool_parameter_schema) in tools.items()])
            body["parallel_tool_calls"] = True

        if web_grounding:
            search_tool = {
                'type': 'web_search_preview',
            }
            if country_code := self.env.company.country_id.code:
                search_tool['user_location'] = {
                    'type': 'approximate',
                    'country': country_code,
                }
                if city := self.env.company.city:
                    search_tool['user_location']['city'] = city
            body.setdefault("tools", []).append(search_tool)

        with api_call_logging(body["input"], tools) as record_response:
            response, to_call, next_inputs, request_token_usage = self._request_llm_openai_helper(body, tools, inputs)
            if record_response:
                record_response(to_call, response, request_token_usage)
            return response, to_call, next_inputs

    def _to_open_ai_tool_schema(self, schema):
        if self.provider != "openai":
            return schema

        for tool in schema:
            required = tool["parameters"]["required"]
            non_required = set(tool["parameters"]["properties"]) - set(required)
            for name in non_required:
                tool["parameters"]["properties"][name]["type"] = [tool["parameters"]["properties"][name]["type"], "null"]
            tool["parameters"]["required"].extend(non_required)
            tool["parameters"]["additionalProperties"] = False
        return schema

    def _request_llm_openai_helper(self, body, tools=None, inputs=()):
        llm_response = self._request(
            method="post",
            endpoint="/responses",
            headers=self._get_base_headers(),
            body=body,
        )

        to_call = []
        response = []
        next_inputs = list(inputs or ())

        output_lines = llm_response.get("output") or []
        has_tool_calls = any(line.get('type') == 'function_call' for line in output_lines)

        for line in output_lines:
            if line.get('type') == 'function_call':
                tool_name = line.get("name", "")

                try:
                    arguments = json.loads(line.get("arguments") or "")
                except json.decoder.JSONDecodeError:
                    _logger.error("AI: Malformed arguments: %s", line)
                    continue

                to_call.append((tool_name, line.get('call_id'), arguments))
                next_inputs.append(line)

            elif not has_tool_calls:
                if text := line.get('text'):
                    response.append(text)
                elif line.get('type') == 'message':
                    response.extend(t for c in line.get('content', ()) if (t := c.get('text')))

        request_token_usage = {}
        if usage := llm_response.get('usage'):
            request_token_usage["input_tokens"] = usage.get("input_tokens", 0)
            request_token_usage["cached_tokens"] = usage.get('input_tokens_details', {}).get('cached_tokens', 0)
            request_token_usage["output_tokens"] = usage.get("output_tokens", 0)

        return response, to_call, next_inputs, request_token_usage

    def _request(
        self, method: str, endpoint: str, headers: dict[str, str], body: dict,
        data: dict | None = None, files: dict | None = None, params: dict | None = None,
        base_url: str | None = None, timeout: int = 30
    ) -> dict:
        route = f"{base_url or self.base_url}/{endpoint.strip('/')}"
        try:
            response = requests.request(
                method,
                route,
                params=params,
                headers=headers,
                json=body,
                data=data,
                timeout=timeout,
                files=files
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            error = repr(e)
            if e.response is not None:
                try:
                    response = e.response.json()
                    if isinstance(response, list) and response:
                        # Gemini return error in a list
                        response = response[0]
                    if isinstance(response, dict) and (json_error := response.get('error', {}).get('message')):
                        error = json_error
                    else:
                        error = json.dumps(response, indent=2)
                except ValueError:
                    error = e.response.text
                if not error:
                    error = repr(e)

            _logger.warning("LLM API request failed: %s", error)
            raise UserError(error)

    def _request_llm_deepseek(
            self, llm_model: str, system_prompts: list[str], user_prompts: list[str],
            tools: dict | None = None, files: list[dict] | None = None, schema: dict | None = None,
            temperature: float = 0.2, inputs: list[dict] | None = None, web_grounding: bool = False
    ):
        messages = []
        for prompt in system_prompts:
            messages.append({"role": "system", "content": prompt})

        user_content = "\n".join(user_prompts)
        if files:
            file_texts = [f.get("value", "") for f in files if f.get("mimetype") == "text/plain"]
            if file_texts:
                user_content += "\n\n[Attached Files Content]:\n" + "\n".join(file_texts)

        messages.append({"role": "user", "content": user_content})

        if inputs:
            for input_item in inputs:
                if isinstance(input_item, dict):
                    messages.append(input_item)

        body = {
            "model": llm_model or "deepseek-chat",
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }

        if schema:
            body["response_format"] = {"type": "json_object"}

        if tools:
            body["tools"] = []
            for tool_name, (tool_desc, _, _, tool_params) in tools.items():
                if "properties" in tool_params:
                    tool_params["additionalProperties"] = False
                    all_properties = list(tool_params["properties"].keys())
                    tool_params["required"] = all_properties

                body["tools"].append({
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": tool_desc,
                        "parameters": tool_params,
                        "strict": True
                    }
                    })

        llm_response = self._request(
            method="post",
            endpoint="/chat/completions",
            headers=self._get_base_headers(),
            body=body,
            base_url=self.base_url
        )

        to_call = []
        response = []
        next_inputs = list(inputs or [])
        request_token_usage = {}

        if choices := llm_response.get("choices"):
            message = choices[0].get("message", {})

            next_inputs.append(message)

            if tool_calls := message.get("tool_calls"):
                for tool_call in tool_calls:
                    tool_name = tool_call["function"]["name"]
                    tool_call_id = tool_call["id"]
                    arguments_str = tool_call["function"]["arguments"]
                    try:
                        arguments = json.loads(arguments_str)
                    except json.decoder.JSONDecodeError as e:
                        _logger.error("AI: Malformed arguments for %s: %s", tool_name, arguments_str)
                        continue
                    to_call.append((tool_name, tool_call_id, arguments))

            if content := message.get("content"):
                response.append(content)

        if usage := llm_response.get("usage"):
            request_token_usage["input_tokens"] = usage.get("prompt_tokens", 0)
            request_token_usage["cached_tokens"] = usage.get("prompt_cache_hit_tokens", 0)
            request_token_usage["output_tokens"] = usage.get("completion_tokens", 0)

        return response, to_call, next_inputs

    def _request_llm_qwen(
            self, llm_model: str, system_prompts: list[str], user_prompts: list[str],
            tools: dict | None = None, files: list[dict] | None = None, schema: dict | None = None,
            temperature: float = 0.2, inputs: list[dict] | None = None, web_grounding: bool = False
    ):
        messages = []
        for prompt in system_prompts:
            messages.append({"role": "system", "content": prompt})

        user_content = "\n".join(user_prompts)
        if files:
            file_texts = [f.get("value", "") for f in files if f.get("mimetype") == "text/plain"]
            if file_texts:
                user_content += "\n\n[Attached Files Content]:\n" + "\n".join(file_texts)

        messages.append({"role": "user", "content": user_content})

        if inputs:
            for input_item in inputs:
                if isinstance(input_item, dict):
                    messages.append(input_item)

        body = {
            "model": llm_model or "qwen-plus-latest",
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }

        if tools:
            body["tools"] = []
            for tool_name, (tool_desc, _, _, tool_params) in tools.items():
                function_def = {
                    "name": tool_name,
                    "description": tool_desc,
                    "parameters": tool_params,
                }
                body["tools"].append({
                    "type": "function",
                    "function": function_def,
                })

        if web_grounding:
            body["enable_search"] = True

        llm_response = self._request(
            method="post",
            endpoint="/chat/completions",
            headers=self._get_base_headers(),
            body=body,
            base_url=self.base_url
        )

        to_call = []
        response = []
        next_inputs = list(inputs or [])
        request_token_usage = {}

        if choices := llm_response.get("choices"):
            message = choices[0].get("message", {})
            next_inputs.append(message)

            if tool_calls := message.get("tool_calls"):
                for tool_call in tool_calls:
                    tool_name = tool_call["function"]["name"]
                    tool_call_id = tool_call["id"]
                    arguments_str = tool_call["function"]["arguments"]
                    try:
                        arguments = json.loads(arguments_str)
                    except json.decoder.JSONDecodeError as e:
                        _logger.error("AI: Malformed arguments for %s: %s", tool_name, arguments_str)
                        continue
                    to_call.append((tool_name, tool_call_id, arguments))

            if content := message.get("content"):
                response.append(content)

        if usage := llm_response.get("usage"):
            request_token_usage["input_tokens"] = usage.get("prompt_tokens", 0)
            request_token_usage["cached_tokens"] = usage.get("cached_tokens", 0)
            request_token_usage["output_tokens"] = usage.get("completion_tokens", 0)

        return response, to_call, next_inputs

    def _get_base_headers(self) -> dict[str, str]:
        return {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self._get_api_token()}',
        }

    def _get_api_token(self):
        provider_config = {
            "openai": {
                "config_key": "ai_chatbot_enhance.openai_key",
                "env_var": "ODOO_AI_CHATGPT_TOKEN",
            },
            "deepseek": {
                "config_key": "ai_chatbot_enhance.deepseek_key",
                "env_var": "ODOO_AI_DEEPSEEK_TOKEN",
            },
            "google": {
                "config_key": "ai_chatbot_enhance.google_key",
                "env_var": "ODOO_AI_GEMINI_TOKEN",
            },
            "qwen": {
                "config_key": "ai_chatbot_enhance.qwen_key",
                "env_var": "ODOO_AI_QWEN_TOKEN",
            },
        }
        config = provider_config.get(self.provider)
        if config is None:
            raise UserError(_("Unsupported provider '%s'", self.provider))

        if api_key := self.env["ir.config_parameter"].sudo().get_param(config["config_key"]) or os.getenv(config["env_var"]):
            return api_key

        raise UserError(_("No API key set for provider '%s'", self.provider))

    def _request_llm_google(
        self, llm_model, system_prompts, user_prompts, tools=None,
        files=None, schema=None, temperature=0.2, inputs=(), web_grounding=False,
    ):
        if (tools or web_grounding) and schema:
            raise NotImplementedError("Gemini does not support structured output with tools")
        if web_grounding and tools:
            # https://ai.google.dev/gemini-api/docs/function-calling?example=meeting#native-tools
            # see note, live api feature only for the moment
            raise NotImplementedError("Gemini does not support tools with web grounding")
        body = {
            "contents": [],
            "generationConfig": {
                "temperature": temperature,
            },
        }
        if system_prompts:
            body["systemInstruction"] = {
                "parts": [
                    {"text": prompt}
                    for prompt in system_prompts
                ],
            }
        if user_prompts:
            body["contents"].append({
                "role": "user",
                "parts": [
                    {"text": prompt}
                    for prompt in user_prompts
                ],
            })

        body["contents"].extend(inputs)

        if files:
            def _build_file(idx, file):
                if file["mimetype"] == "text/plain":
                    return {"text": file["value"]}

                return {"inline_data": {"mime_type": file['mimetype'], "data": file["value"]}}

            body["contents"].append({"role": "user", "parts":
                [_build_file(idx, file) for idx, file in enumerate(files, start=1)]})

        if schema:
            body["generationConfig"]["responseMimeType"] = "application/json"
            body["generationConfig"]["responseJsonSchema"] = schema

        if tools:
            body["tools"] = {
                "functionDeclarations": [{
                    "description": tool_description,
                    "parameters": tool_parameter_schema,
                    "name": tool_name,
                } for tool_name, (tool_description, __, __, tool_parameter_schema) in tools.items()]
            }
        if web_grounding:
            body["tools"] = {'google_search': {}}

        if llm_model == "gemini-2.5-flash":
            # from testing, increasing thinking budget results in the LLM actually replying ¯\_(ツ)_/¯
            body['generationConfig']["thinkingConfig"] = {
                "thinkingBudget": 512,
            }

        with api_call_logging(body["contents"], tools) as record_response:
            response = None
            to_call = []
            next_inputs = inputs
            request_token_usage = {}
            for attempt in range(3):
                response, to_call, next_inputs, request_token_usage = self._request_llm_google_helper(body, llm_model, inputs)
                if response or to_call:
                    break
                _logger.warning("Gemini failed to generate a response, retrying...")
            if not (response or to_call):
                response = "Error: failed to generate a response, try again later."
            if record_response:
                record_response(to_call, response, request_token_usage)
            return response, to_call, next_inputs

    def _request_llm_google_helper(self, body, llm_model, inputs=()):
        llm_response = self._request(
            method="post",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            headers={"x-goog-api-key": self._get_api_token()},
            endpoint=f"/models/{llm_model}:generateContent",
            params={},
            body=body,
        )

        to_call = []
        response = []
        next_inputs = list(inputs or ())

        candidates = llm_response.get("candidates") or []
        has_tool_calls = any(
            part.get('functionCall')
            for candidate in candidates
            for part in candidate.get('content', {}).get('parts') or []
        )

        for candidate in candidates:
            for line in candidate.get('content', {}).get('parts') or ():
                if f_info := line.get('functionCall'):
                    to_call.append((f_info['name'], f_info['name'], f_info['args']))
                    next_inputs.append({"role": "model", "parts": [line]})
                elif not has_tool_calls:
                    if r := line.get('text'):
                        response.append(r)
                    else:
                        _logger.warning("Gemini: could not parse %s", line)

        request_token_usage = {}
        if usage := llm_response.get("usageMetadata"):
            request_token_usage["input_tokens"] = usage.get("promptTokenCount", 0)
            request_token_usage["cached_tokens"] = usage.get("cachedContentTokenCount", 0)
            request_token_usage["output_tokens"] = usage.get("candidatesTokenCount", 0)

        return response, to_call, next_inputs, request_token_usage

    def get_ai_embedding(
        self,
        input: str | list[str] | list[int] | list[list[int]],
        dimensions: int,
        model: str = 'text-embedding-3-small',
        encoding_format: str | None = None,
        user: str | None = None,
    ) -> EmbeddingResponse:
        check_model_depreciation(self.env, model)
        body = {
            'input': input,
            'model': model
        }
        self._add_if_set(body, 'encoding_format', encoding_format)
        self._add_if_set(body, 'dimensions', dimensions)
        self._add_if_set(body, 'user', user)

        return self._request(
            method='post',
            endpoint='/embeddings',
            headers=self._get_base_headers(),
            body=body,
        )

    def _add_if_set(self, d: dict, key: str, value):
        if value is not None:
            d[key] = value

    def _build_tool_call_response(self, tool_call_id, return_value):
        """Build the response for the given tool call.

        :param tool_call_id: The identifier of the tool call
        :param return_value: The value the tool returned
        """
        if self.provider == "openai":
            return {
                "type": "function_call_output",
                "call_id": tool_call_id,
                "output": str(return_value),
            }

        if self.provider == "google":
            return {
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "name": tool_call_id,
                        "response": {"result": str(return_value)},
                    },
                }],
            }

        if self.provider == "deepseek":
            return {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": str(return_value),
            }

        if self.provider == "qwen":
            return {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": str(return_value),
            }

        raise NotImplementedError(f"Unsupported provider: {self.provider}")
