"""JSON Schemas for POST /v1/grade and POST /v1/fix — copied verbatim
from the ListSmith service's own discovery document (/.well-known/x402
in QuincyGB/list-smith), which is the service's published contract.
Keep these in sync with the service, never fork them.
"""


GRADE_INPUT_SCHEMA = {'type': 'object',
 'required': ['resource_url'],
 'properties': {'resource_url': {'type': 'string',
                                 'description': 'Full URL of the x402 resource to '
                                                'grade, e.g. '
                                                'https://service.example.com/v1/endpoint'},
                'sample_output': {'description': 'Optional: one real response body '
                                                 'from the endpoint. When supplied, '
                                                 'the grade also checks it against the '
                                                 "endpoint's published output "
                                                 'schema.'}}}


GRADE_OUTPUT_SCHEMA = {'type': 'object',
 'required': ['resource_url',
              'graded_at',
              'reachable',
              'http_status',
              'x402_valid',
              'terms',
              'extension_present',
              'sdk_validation',
              'schema_quality',
              'defects',
              'score',
              'fix_available'],
 'properties': {'resource_url': {'type': 'string'},
                'graded_at': {'type': 'string',
                              'description': 'ISO-8601 timestamp of the probe'},
                'reachable': {'type': 'boolean'},
                'http_status': {'type': ['integer', 'null']},
                'x402_valid': {'type': 'boolean'},
                'terms': {'type': 'object',
                          'required': ['found',
                                       'price_usdc',
                                       'amount_units',
                                       'network',
                                       'asset',
                                       'pay_to',
                                       'scheme',
                                       'x402_version'],
                          'properties': {'found': {'type': 'boolean'},
                                         'price_usdc': {'type': ['string', 'null']},
                                         'amount_units': {'type': ['string', 'null']},
                                         'network': {'type': ['string', 'null']},
                                         'asset': {'type': ['string', 'null']},
                                         'pay_to': {'type': ['string', 'null']},
                                         'scheme': {'type': ['string', 'null']},
                                         'x402_version': {'type': ['integer',
                                                                   'null']}}},
                'extension_present': {'type': 'boolean'},
                'sdk_validation': {'type': 'object',
                                   'required': ['ran',
                                                'consistency_valid',
                                                'spec_valid',
                                                'errors'],
                                   'properties': {'ran': {'type': 'boolean',
                                                          'description': 'False when '
                                                                         'there was no '
                                                                         'extension to '
                                                                         'validate'},
                                                  'consistency_valid': {'type': ['boolean',
                                                                                 'null']},
                                                  'spec_valid': {'type': ['boolean',
                                                                          'null']},
                                                  'errors': {'type': 'array',
                                                             'items': {'type': 'string'}}}},
                'schema_quality': {'type': 'object',
                                   'required': ['extension_present',
                                                'hollow',
                                                'score',
                                                'deductions'],
                                   'properties': {'extension_present': {'type': 'boolean'},
                                                  'hollow': {'type': ['boolean',
                                                                      'null']},
                                                  'score': {'type': ['integer',
                                                                     'null']},
                                                  'deductions': {'type': 'array',
                                                                 'items': {'type': 'object',
                                                                           'required': ['name',
                                                                                        'points',
                                                                                        'detail'],
                                                                           'properties': {'name': {'type': 'string'},
                                                                                          'points': {'type': 'integer',
                                                                                                     'minimum': 0},
                                                                                          'detail': {'type': 'string'}}}}}},
                'defects': {'type': 'array',
                            'items': {'type': 'object',
                                      'required': ['name', 'severity', 'detail'],
                                      'properties': {'name': {'type': 'string'},
                                                     'severity': {'type': 'string',
                                                                  'enum': ['critical',
                                                                           'major',
                                                                           'minor']},
                                                     'detail': {'type': 'string'}}}},
                'score': {'type': 'integer',
                          'minimum': 0,
                          'maximum': 100,
                          'description': 'Listing grade: 100 (clean) to 0, from the '
                                         'itemized defects'},
                'fix_available': {'type': 'boolean',
                                  'description': 'True when the endpoint presents a '
                                                 'valid challenge /v1/fix can repair '
                                                 'against'}}}


FIX_INPUT_SCHEMA = {'type': 'object',
 'required': ['resource_url', 'sample_output'],
 'properties': {'resource_url': {'type': 'string',
                                 'description': 'Full URL of the x402 resource to '
                                                'repair, e.g. '
                                                'https://service.example.com/v1/endpoint'},
                'sample_output': {'description': 'One real response body from the '
                                                 'endpoint (required). The repaired '
                                                 'output schema is inferred from this '
                                                 'sample only — nothing is invented.'},
                'name': {'type': 'string',
                         'description': 'Optional display name for the generated '
                                        'description; derived from the URL when '
                                        'omitted.'},
                'category': {'type': 'string',
                             'description': 'Optional category (e.g. token-risk, '
                                            'search, data, media, monitoring) used to '
                                            "phrase the description's use-when "
                                            'sentence.'}}}


FIX_OUTPUT_SCHEMA = {'type': 'object',
 'required': ['resource_url',
              'graded_at',
              'ok',
              'failure',
              'score_before',
              'defects',
              'package',
              'notes'],
 'properties': {'resource_url': {'type': 'string'},
                'graded_at': {'type': 'string'},
                'ok': {'type': 'boolean'},
                'failure': {'type': ['string', 'null'],
                            'description': 'Why no package was shipped, when ok is '
                                           'false — an unvalidatable package is never '
                                           'shipped broken'},
                'score_before': {'type': ['integer', 'null'],
                                 'description': 'The /v1/grade score of the listing as '
                                                'found'},
                'defects': {'type': 'array',
                            'items': {'type': 'object',
                                      'required': ['name', 'severity', 'detail'],
                                      'properties': {'name': {'type': 'string'},
                                                     'severity': {'type': 'string',
                                                                  'enum': ['critical',
                                                                           'major',
                                                                           'minor']},
                                                     'detail': {'type': 'string'}}}},
                'package': {'type': ['object', 'null'],
                            'properties': {'inferred_from': {'type': 'string',
                                                             'const': 'single_sample'},
                                           'description': {'type': 'string',
                                                           'description': 'Under 500 '
                                                                          'chars, with '
                                                                          'a use-when '
                                                                          'sentence'},
                                           'extension': {'type': 'object',
                                                         'description': 'The repaired '
                                                                        'Bazaar '
                                                                        'discovery '
                                                                        'extension '
                                                                        '(passed both '
                                                                        'SDK '
                                                                        'validators)'},
                                           'openapi_snippet': {'type': 'object'},
                                           'well_known_snippet': {'type': 'object'},
                                           'instructions': {'type': 'string'},
                                           'validation': {'type': 'object',
                                                          'required': ['consistency_valid',
                                                                       'spec_valid'],
                                                          'properties': {'consistency_valid': {'type': 'boolean'},
                                                                         'spec_valid': {'type': 'boolean'}}}},
                            'required': ['inferred_from',
                                         'description',
                                         'extension',
                                         'openapi_snippet',
                                         'well_known_snippet',
                                         'instructions',
                                         'validation']},
                'notes': {'type': 'array', 'items': {'type': 'string'}}}}
