import unittest

from fastapi.testclient import TestClient

from mimo2api.web_service import app, build_route_diagnostics


class ResponsesConverterStabilityTests(unittest.TestCase):
    def test_sse_event_does_not_mutate_input_payload(self):
        from mimo2api.responses_converter import _sse_event

        payload = {"response": {"status": "in_progress"}}
        event = _sse_event("response.created", payload)
        self.assertIn("response.created", event)
        self.assertNotIn("type", payload)


class ResponsesToolCompatibilityTests(unittest.TestCase):
    def test_convert_request_accepts_responses_and_chat_style_function_tools(self):
        from mimo2api.responses_converter import convert_request

        converted = convert_request({
            "model": "mimo-v2.5",
            "input": "hi",
            "tools": [
                {"type": "function", "name": "lookup", "description": "Lookup", "parameters": {"type": "object"}},
                {"type": "function", "function": {"name": "chat_style", "parameters": {"type": "object"}}},
                {"type": "web_search_preview"},
                {"type": "function", "parameters": {"type": "object"}},
            ],
        })

        self.assertEqual([tool["function"]["name"] for tool in converted["tools"]], ["lookup", "chat_style"])
        self.assertEqual(converted["messages"][0]["content"], "hi")


class ResponsesStreamingCompatibilityTests(unittest.TestCase):
    def test_stream_converter_emits_in_progress_and_output_text_done(self):
        import json
        from mimo2api.responses_converter import ResponsesStreamConverter

        converter = ResponsesStreamConverter(model="mimo-v2.5")
        events = []
        events.extend(converter.process_chunk("data: " + json.dumps({
            "choices": [{"delta": {"role": "assistant", "content": "hello"}}]
        })))
        events.extend(converter.process_chunk("data: " + json.dumps({
            "choices": [{"delta": {}, "finish_reason": "stop"}]
        })))
        events.extend(converter.process_chunk("data: [DONE]"))
        joined = "".join(events)
        self.assertIn("event: response.in_progress", joined)
        self.assertIn("event: response.output_text.done", joined)
        self.assertIn('"text": "hello"', joined)

    def test_response_input_image_url_object_is_normalized(self):
        from mimo2api.responses_converter import convert_request

        converted = convert_request({
            "model": "mimo-v2.5",
            "input": [{
                "type": "message",
                "role": "user",
                "content": [{"type": "input_image", "image_url": {"url": "data:image/png;base64,abc"}}],
            }],
        })
        content = converted["messages"][0]["content"]
        self.assertEqual(content[0]["image_url"]["url"], "data:image/png;base64,abc")


class ResponsesBoundaryCompatibilityTests(unittest.TestCase):
    def test_function_call_output_history_is_stringified_as_tool_message(self):
        from mimo2api.responses_converter import convert_request

        converted = convert_request({
            "model": "mimo-v2.5",
            "input": [
                {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": {"q": "x"}},
                {"type": "function_call_output", "call_id": "call_1", "output": {"result": 7}},
            ],
        })
        self.assertEqual(converted["messages"][0]["role"], "assistant")
        self.assertEqual(converted["messages"][0]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(converted["messages"][0]["tool_calls"][0]["function"]["arguments"], '{"q": "x"}')
        self.assertEqual(converted["messages"][1]["role"], "tool")
        self.assertEqual(converted["messages"][1]["tool_call_id"], "call_1")
        self.assertEqual(converted["messages"][1]["content"], '{"result": 7}')

    def test_tool_choice_and_max_output_tokens_are_mapped(self):
        from mimo2api.responses_converter import convert_request

        converted = convert_request({
            "model": "mimo-v2.5",
            "input": "hi",
            "max_output_tokens": 123,
            "tool_choice": {"type": "function", "name": "lookup"},
            "tools": [{"type": "function", "name": "lookup", "parameters": {"type": "object"}}],
        })
        self.assertEqual(converted["max_tokens"], 123)
        self.assertEqual(converted["tool_choice"], {"type": "function", "function": {"name": "lookup"}})



class ResponsesToolArgumentNormalizationTests(unittest.TestCase):
    def test_invalid_function_call_arguments_are_wrapped_as_json(self):
        import json
        from mimo2api.responses_converter import convert_request

        converted = convert_request({
            "model": "mimo-v2.5",
            "input": [
                {"type": "function_call", "call_id": "call_bad", "name": "shell", "arguments": "ls -la"},
            ],
        })

        args = converted["messages"][0]["tool_calls"][0]["function"]["arguments"]
        self.assertEqual(json.loads(args), {"_raw_arguments": "ls -la"})

    def test_custom_tool_call_input_is_wrapped_as_input_json(self):
        import json
        from mimo2api.responses_converter import convert_request

        converted = convert_request({
            "model": "mimo-v2.5",
            "input": [
                {"type": "custom_tool_call", "call_id": "call_custom", "name": "terminal", "input": "ls -la"},
            ],
        })

        args = converted["messages"][0]["tool_calls"][0]["function"]["arguments"]
        self.assertEqual(json.loads(args), {"input": "ls -la"})

    def test_valid_json_string_arguments_are_preserved(self):
        from mimo2api.responses_converter import convert_request

        raw_args = '{"q":"x"}'
        converted = convert_request({
            "model": "mimo-v2.5",
            "input": [
                {"type": "function_call", "call_id": "call_ok", "name": "lookup", "arguments": raw_args},
            ],
        })

        args = converted["messages"][0]["tool_calls"][0]["function"]["arguments"]
        self.assertEqual(args, raw_args)

    def test_all_converted_tool_call_arguments_are_json_loadable(self):
        import json
        from mimo2api.responses_converter import convert_request

        converted = convert_request({
            "model": "mimo-v2.5",
            "input": [
                {"type": "function_call", "call_id": "call_dict", "name": "dict_args", "arguments": {"q": "x"}},
                {"type": "function_call", "call_id": "call_list", "name": "list_args", "arguments": [1, 2]},
                {"type": "function_call", "call_id": "call_num", "name": "num_args", "arguments": 7},
                {"type": "function_call", "call_id": "call_bool", "name": "bool_args", "arguments": False},
                {"type": "function_call", "call_id": "call_null", "name": "null_args", "arguments": None},
                {"type": "function_call", "call_id": "call_json", "name": "json_args", "arguments": "{\"ok\":true}"},
                {"type": "function_call", "call_id": "call_raw", "name": "raw_args", "arguments": "unterminated{"},
                {"type": "custom_tool_call", "call_id": "call_custom", "name": "custom", "input": "ls -la"},
            ],
        })

        for message in converted["messages"]:
            for tool_call in message.get("tool_calls", []):
                json.loads(tool_call["function"]["arguments"])
