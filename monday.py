"""Monday.com GraphQL API client."""
import json
import httpx

MONDAY_URL = "https://api.monday.com/v2"
API_VERSION = "2023-10"

_BOARD_QUERY = """
query GetBoard($boardId: ID!) {
  boards(ids: [$boardId]) {
    id
    name
    columns { id title type settings_str }
    groups { id title color }
    items_page(limit: 500) {
      items {
        id
        name
        group { id }
        column_values { id text value }
      }
    }
  }
}
"""

_CHANGE_MUTATION = """
mutation ChangeStatus($boardId: ID!, $itemId: ID!, $columnId: String!, $value: String!) {
  change_simple_column_value(board_id: $boardId, item_id: $itemId, column_id: $columnId, value: $value) {
    id
  }
}
"""


async def _gql(token: str, query: str, variables: dict) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            MONDAY_URL,
            headers={
                "Authorization": token,
                "Content-Type": "application/json",
                "API-Version": API_VERSION,
            },
            json={"query": query, "variables": variables},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if "errors" in data:
            raise ValueError(str(data["errors"]))
        return data.get("data", {})


def _parse_status_options(settings_str: str) -> list:
    try:
        s = json.loads(settings_str or "{}")
        labels    = s.get("labels", {})
        colors    = s.get("labels_colors", {})
        positions = s.get("labels_positions_v2", {})
        options   = []
        for idx, label in labels.items():
            color = colors.get(idx, {}).get("color", "#c4c4c4")
            pos   = positions.get(idx, 999)
            options.append({"index": idx, "label": label, "color": color, "pos": pos})
        options.sort(key=lambda x: x["pos"])
        for o in options:
            del o["pos"]
        return options
    except Exception:
        return []


async def get_board(token: str, board_id: str) -> dict:
    data   = await _gql(token, _BOARD_QUERY, {"boardId": board_id})
    boards = data.get("boards") or []
    if not boards:
        raise ValueError("Board nicht gefunden")
    b = boards[0]

    status_cols = {}
    for col in b.get("columns", []):
        if col["type"] in ("color", "status"):
            status_cols[col["id"]] = {
                "id":      col["id"],
                "title":   col["title"],
                "options": _parse_status_options(col.get("settings_str", "{}")),
            }

    groups_map = {g["id"]: {**g, "items": []} for g in b.get("groups", [])}

    for item in (b.get("items_page") or {}).get("items", []):
        gid = (item.get("group") or {}).get("id")
        if gid not in groups_map:
            continue
        columns = {}
        for cv in item.get("column_values", []):
            if cv["id"] not in status_cols:
                continue
            val_str = cv.get("value") or "{}"
            try:
                idx = str(json.loads(val_str).get("index", ""))
            except Exception:
                idx = ""
            columns[cv["id"]] = {"text": cv.get("text") or "", "index": idx}
        groups_map[gid]["items"].append({
            "id":      item["id"],
            "name":    item["name"],
            "columns": columns,
        })

    return {
        "id":             b["id"],
        "name":           b["name"],
        "status_columns": list(status_cols.values()),
        "groups":         [g for g in groups_map.values() if g["items"]],
    }


async def set_status(token: str, board_id: str, item_id: str, column_id: str, label: str) -> None:
    await _gql(token, _CHANGE_MUTATION, {
        "boardId":  board_id,
        "itemId":   item_id,
        "columnId": column_id,
        "value":    label,
    })


_CREATE_MUTATION = """
mutation CreateItem($boardId: ID!, $groupId: String!, $name: String!) {
  create_item(board_id: $boardId, group_id: $groupId, item_name: $name) {
    id
    name
    group { id }
    column_values { id text value }
  }
}
"""


async def create_item(token: str, board_id: str, group_id: str, name: str) -> dict:
    data = await _gql(token, _CREATE_MUTATION, {
        "boardId":  board_id,
        "groupId":  group_id,
        "name":     name,
    })
    return data.get("create_item") or {}
