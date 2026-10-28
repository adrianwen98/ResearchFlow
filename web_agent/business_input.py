"""Shared model-facing schema; runtime validation lives in comparison_contract.

Keep dict inputs unchanged for legacy layout adaptation and precise diagnostics.
The identical schema is exposed by both saving and exporting tools.
"""
from typing import Annotated
from pydantic import Field,BaseModel
from .delivery_schema import UNITS

# These are canonical target units accepted by field_for(). Legacy input aliases
# remain supported by the runtime comparison contract; model output is nudged
# toward one canonical spelling to avoid wasted validation attempts.
FIELD_UNITS = [*UNITS, "USD", "SGD", "CNY", "EUR", "GBP", "JPY"]

FACT={'type':'object','required':['value'],'properties':{
    'value':{'type':['string','number','boolean','null']},
    'raw_value':{'type':['string','number','boolean','null']},
    'input_unit':{'type':'string','description':'Original unit, including common Chinese/English aliases. Must match quoted quantity.'},
    'source_id':{'type':'string','description':'Existing source ID; URL/observation/status are validated by the program.'},
    'quote':{'type':'string','description':'Continuous exact excerpt from the saved observation.'},
    'status':{'type':'string','enum':['verified','unverified','not_disclosed']},
    'reason':{'type':'string'},
    'context_quote':{'type':'string'},
    'evidence_subject':{'type':'string', 'description':('Optional exact H1 taken from the same saved webpage observation. '
                                                     'Do not invent or translate it. The host independently checks the real observed H1.')},
    'price':{'type':'object','properties':{
        'currency':{'type':'string','pattern':'^[A-Z]{3}$',
                    'description':'ISO 4217 currency code, evidenced in the source (e.g. CNY, USD).'},
        'market':{'type':'string','pattern':'^[A-Z]{2}$',
                  'description':'ISO country code, evidenced by source URL locale or exact context_quote.'},
        'type':{'type':'string','enum':['launch','current','starting','promotional'],
                'description':'Historical launch price, current offer, starting price, or promotion; do not conflate them.'},
        'queried_at':{'type':'string','pattern':r'^\d{4}-\d{2}-\d{2}$',
                      'description':'YYYY-MM-DD; must equal the recorded source access date.'}
    }}
}}
COMPARISON={'type':'object','required':['name','mode','columns','rows'],'properties':{
    'name':{'type':'string'},'mode':{'const':'comparison'},
    'columns':{'type':'array','items':{'type':'string'},'uniqueItems':True},
    'fields':{'type':'object','additionalProperties':{'type':'object','properties':{
        'type':{'enum':['text','number','price','boolean','date']},
        'unit':{'type':'string','enum':FIELD_UNITS,
                'description':('Use one canonical target unit from this enum, never a free-form unit (e.g. 核). '
                               'For quantities whose source unit is unsupported, use an exact text value '
                               'or null with reason. Currency codes are for price fields.')},
        'required':{'type':'boolean'},
        'role':{'type':'string','enum':['verbatim_excerpt','document_identity','editorial_label'],
                'description':('verbatim_excerpt: unchanged observed source text (up to 1500 characters). '
                               'document_identity: identify the document/version using its saved unique H1. '
                               'editorial_label: nonfactual topic/category label, not a verified statement.')}}}},
    'rows':{'type':'array','items':{'type':'object','properties':{
        'label':{'type':'string','description':'Stable unique object identity, not batch position.'},
        'entity':{'type':'object','properties':{'id':{'type':'string'},'name':{'type':'string'}}},
        'cells':{'type':'object','additionalProperties':FACT}},'required':['cells']}}
}}
BusinessSheets=Annotated[list[dict],Field(json_schema_extra={'items':{'anyOf':[
    COMPARISON,{'type':'object','required':['name','mode'],'properties':{
        'name':{'type':'string'},
        'mode':{'enum':['table','calculation']},
        'filename':{'type':'string'},
        'calculation_id':{'type':'string'}}}]}})]

class BusinessInput(BaseModel):
    sheets:BusinessSheets
