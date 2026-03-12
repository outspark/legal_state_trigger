from typing_extensions import TypedDict, Annotated; from typing import List; class LST_Node: pass; class State(TypedDict): extracted_lsts: Annotated[List[LST_Node], "docs"];
def func(state: State): lsts = state.get("extracted_lsts", []); [i for i, a in enumerate(lsts)]
