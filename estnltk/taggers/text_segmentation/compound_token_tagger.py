#
#   CompoundTokenTagger analyzes tokens and decides, which
#  tokens should be joined together (as compound_tokens).
#  In later analysis, tokens and compound_tokens layers 
#  are used for creating the words layer.
# 

import regex as re
import os
from typing import Union

from pandas import read_csv
from pandas.io.common import EmptyDataError

from estnltk.core import PACKAGE_PATH

from estnltk import EnvelopingSpan
from estnltk.text import Layer, SpanList
from estnltk.taggers import Tagger
from estnltk.taggers import RegexTagger
from estnltk.taggers import DisambiguatingTagger
from estnltk.layer_operations import resolve_conflicts
from estnltk.rewriting import MorphAnalyzedToken
from .patterns import MACROS
from .patterns import email_and_www_patterns, emoticon_patterns, xml_patterns
from .patterns import unit_patterns, number_patterns, abbreviations_before_initials_patterns
from .patterns import initial_patterns, abbreviation_patterns
from .patterns import case_endings_patterns, number_fixes_patterns

# Pattern for checking whether the string contains any letters
_letter_pattern = re.compile(r'''([{LETTERS}]+)'''.format(**MACROS), re.X)

# List containing words that should be ignored during the normalization of words with hyphens
DEFAULT_IGNORE_LIST = os.path.join( PACKAGE_PATH, 'rewriting', 'premorph', 'rules_files', 'ignore.csv')


class CompoundTokenTagger(Tagger):
    """Tags adjacent tokens that should be analyzed as one word."""
    output_attributes = ('type', 'normalized')
    output_layer = 'compound_tokens'
    input_layers = ['tokens']
    custom_abbreviations = []
    conf_param = [ 'custom_abbreviations', 'ignored_words', 'tag_numbers', 'tag_units',
                   'tag_email_and_www', 'tag_emoticons', 'tag_xml', 'tag_initials',
                   'tag_abbreviations', 'tag_case_endings', 'tag_hyphenations',
                   'use_custom_abbreviations', 'do_not_join_on_strings',
                   # Inner parameters
                   '_tokenization_hints_tagger_1', '_tokenization_hints_tagger_2',
                   '_conflict_resolving_strategy', '_input_tokens_layer',
                   # For backward compatibility:
                   'depends_on', 'layer_name'
                  ]
    layer_name = output_layer   # <- For backward compatibility ...
    depends_on = input_layers   # <- For backward compatibility ...
    
    def __init__(self,
                 output_layer:str='compound_tokens',
                 input_tokens_layer:str='tokens',
                 tag_numbers: bool = True,
                 tag_units: bool = True,
                 tag_email_and_www: bool = True,
                 tag_emoticons: bool = True,
                 tag_xml: bool = True,
                 tag_initials: bool = True,
                 tag_abbreviations: bool = True,
                 tag_case_endings: bool = True,
                 tag_hyphenations: bool = True,
                 custom_abbreviations: list = (),
                 do_not_join_on_strings: list = ('\n\n',),
                 ):
        """Initializes CompoundTokenTagger.

        Parameters
        ----------
        output_layer: str (default: 'compound_tokens')
            Name for the compound tokens layer;
        
        input_tokens_layer: str (default: 'tokens')
            Name of the input tokens layer;
        
        tag_numbers: boolean (default: True)
            Numeric expressions with decimal separators, numbers with 
            digit group separators, and common date and time expressions
            will be joined into compound tokens.

        tag_units: boolean (default: True)
            x-per-y style units that follow numeric expressions will 
            be joined into compound tokens.

        tag_email_and_www: boolean (default: True)
            E-mail addresses and web addresses will be joined into 
            compound tokens.

        tag_emoticons: boolean (default: True)
            Most common emoticons will be detected and joined into 
            compound tokens.

        tag_xml: boolean (default: True)
            Symbols making up an XML tag will be joined into compound
            tokens.

        tag_initials: boolean (default: True)
            Names starting with initials will be joined into compound
            tokens.

        tag_abbreviations: boolean (default: True)
            Abbreviations (and accompanying punctuation symbols) will 
            be joined into compound tokens.

        tag_case_endings: boolean (default: True)
            Morphological case endings (separated from words with 
            hyphens or other punctuation) will be joined into compound 
            tokens with the preceding tokens.

        tag_hyphenations: boolean (default: True)
            Hyphenated/syllabified words (such as 'ka-su-lik'), stretched 
            out words (such as 'vää-ää-ääga'), and compound nouns with
            hyphens (such as 'Vana-Hiina', 'Mari-Liis') will be joined 
            into compound tokens.

        custom_abbreviations: list (default: [])
            A list of user-defined abbreviations (strings), which need 
            to be joined with accompanying punctuation symbols into 
            compound tokens. This can be used to enhance the built-in 
            list of abbreviations.
            Note that user-defined abbreviations must be strings that 
            TokensTagger does not split into smaller tokens.
        
        do_not_join_on_strings: list (default: ['\n\n'])
            A list of separator strings that will cancel the creation 
            of a compound token if any of them happens to be inside 
            the compound token.
            If you have separated sentences and paragraphs in text with 
            special strings (e.g. sentences by '\n', and paragraphs by 
            '\n\n'), then you can use this list to discard compound tokens 
            annotations at the locations of sentence and paragraph 
            boundaries.
            By default, the list only contains '\n\n', intending to keep 
            compound tokens off from the paragraph boundaries.
        """
        # Set input/output parameters
        self.output_layer = output_layer
        self._input_tokens_layer = input_tokens_layer
        self.input_layers = [input_tokens_layer]
        self.layer_name = self.output_layer  # <- For backward compatibility ...
        self.depends_on = self.input_layers  # <- For backward compatibility ...
        # Set tagging configuration
        conflict_resolving_strategy = 'MAX'
        self.tag_numbers = tag_numbers
        self.tag_units = tag_units
        self.tag_email_and_www = tag_email_and_www
        self.tag_emoticons = tag_emoticons
        self.tag_xml = tag_xml
        self.tag_initials = tag_initials
        self.tag_abbreviations = tag_abbreviations
        self.tag_case_endings = tag_case_endings
        self.tag_hyphenations = tag_hyphenations
        self.use_custom_abbreviations = bool(custom_abbreviations)
        self._conflict_resolving_strategy = conflict_resolving_strategy
        if custom_abbreviations and not tag_abbreviations:
            raise ValueError("(!) List of custom_abbreviations given, but tag_abbreviations is switched off.")
        assert isinstance(custom_abbreviations, (list, tuple))
        self.custom_abbreviations = custom_abbreviations
        assert isinstance(do_not_join_on_strings, (list, tuple))
        self.do_not_join_on_strings = do_not_join_on_strings
        # =========================
        #  1st level hints tagger
        # =========================
        _vocabulary_1 = [] 
        if tag_numbers:
            _vocabulary_1.extend(number_patterns)
        if tag_units:
            _vocabulary_1.extend(unit_patterns)
        if tag_xml:
            _vocabulary_1.extend(xml_patterns)
        if tag_email_and_www:
            _vocabulary_1.extend(email_and_www_patterns)
        if tag_emoticons:
            _vocabulary_1.extend(emoticon_patterns)
        if tag_abbreviations:
           # Some abbreviations need to be captured before initials to
           # prevent a mixup between abbreviations and initials;
           _vocabulary_1.extend(abbreviations_before_initials_patterns)
        if tag_initials:
            _vocabulary_1.extend(initial_patterns)
        if tag_abbreviations:
            _vocabulary_1.extend(abbreviation_patterns)
        self._tokenization_hints_tagger_1 = RegexTagger(vocabulary=_vocabulary_1,
                                                        output_attributes=('normalized', '_priority_', 'pattern_type'),
                                                        conflict_resolving_strategy=conflict_resolving_strategy,
                                                        priority_attribute='_priority_',
                                                        overlapped=False,
                                                        output_layer='tokenization_hints',
                                                        )
        # =========================
        #  2nd level hints tagger
        # =========================
        _vocabulary_2 = []
        if tag_case_endings:
            _vocabulary_2.extend(case_endings_patterns)
        if tag_numbers:
            _vocabulary_2.extend(number_fixes_patterns)
        self._tokenization_hints_tagger_2 = None
        if _vocabulary_2:
            self._tokenization_hints_tagger_2 = RegexTagger(vocabulary=_vocabulary_2,
                                                            output_attributes=('normalized', '_priority_', 'pattern_type',
                                                            'left_strict', 'right_strict'),
                                                            conflict_resolving_strategy=conflict_resolving_strategy,
                                                            priority_attribute='_priority_',
                                                            overlapped=False,
                                                            output_layer='tokenization_hints',
                                                            )
        # Load words that should be ignored during normalization of words with hyphens
        self.ignored_words = self._load_ignore_words_from_csv( DEFAULT_IGNORE_LIST )



    def _make_layer(self, raw_text: str, layers, status: dict):
        """Creates compound_tokens layer.
        
        Parameters
        ----------
        raw_text: str
           Text string corresponding to the text in which 
           compound token analysis will be performed;
          
        layers: MutableMapping[str, Layer]
           Layers of the raw_text. Contains mappings from the 
           name of the layer to the Layer object. Must contain
           the tokens layer.
          
        status: dict
           This can be used to store metadata on layer tagging.
        """
        compound_tokens_lists = []
        # 1) Apply RegexTagger in order to get hints for the 1st level tokenization
        conflict_status = {}
        tokenization_hints = {}
        new_layer = self._tokenization_hints_tagger_1.make_layer(raw_text, layers, status=conflict_status)
        for sp in new_layer.span_list:
            #print('*',text.text[sp.start:sp.end], sp.pattern_type, sp.normalized)
            if hasattr(sp, 'pattern_type') and sp.pattern_type.startswith('negative:'):
                # This is a negative pattern (used for preventing other patterns from matching),
                # and thus should be discarded altogether ...
                continue
            end_node = {'end': sp.end}
            if hasattr(sp, 'pattern_type'):
                end_node['pattern_type'] = sp.pattern_type
            if hasattr(sp, 'normalized'):
                end_node['normalized'] = sp.normalized
            # Note: we assume that all conflicts have been resolved by 
            # RegexTagger, that is -- exactly one (compound) token begins
            # from one starting position ...
            if sp.start in tokenization_hints:
                raise Exception('(!) Unexpected overlapping tokenization hints: ',
                                [raw_text[sp2.start:sp2.end] for sp2 in new_layer.span_list])
            # Discard the hint if the compound token would contain any of the disallowed strings
            if self.do_not_join_on_strings:
                discard = False
                for separator in self.do_not_join_on_strings:
                    if separator in raw_text[sp.start:sp.end]:
                       discard = True
                if discard:
                    continue
            tokenization_hints[sp.start] = end_node

        hyphenation_status = None
        last_end = None
        tag_hyphenations = self.tag_hyphenations
        # 2) Apply tokenization hints + hyphenation correction
        for i, token_span in enumerate(layers[ self._input_tokens_layer ]):
            #token = token_span.text
            # 2.1) Apply detection of custom non-ending abbreviations
            #      (and override the previous compound token in case
            #       of an overlap)
            if self.custom_abbreviations:
                self._try_to_add_custom_abbreviation(raw_text, layers, i, compound_tokens_lists)
            
            # 2.2) Check for tokenization hints
            if token_span.start in tokenization_hints:
                # Find where the new compound token should end 
                end_token_index = None
                for j in range(i, len(layers[ self._input_tokens_layer ]) ):
                    if layers[ self._input_tokens_layer ][j].end == tokenization_hints[token_span.start]['end']:
                        end_token_index = j
                    elif tokenization_hints[token_span.start]['end'] < layers[ self._input_tokens_layer ][j].start:
                        break
                if end_token_index:
                    spans = layers[ self._input_tokens_layer ][i:end_token_index+1]
                    spl = EnvelopingSpan(spans=spans)
                    spl.type = ('tokenization_hint',)
                    spl.normalized = None
                    if 'pattern_type' in tokenization_hints[token_span.start]:
                        spl.type = (tokenization_hints[token_span.start]['pattern_type'],)
                    if 'normalized' in tokenization_hints[token_span.start]:
                        spl.normalized = tokenization_hints[token_span.start]['normalized']
                    add_compound_token = True
                    if self.custom_abbreviations:
                        # if custom abbreviations are also used, we have to eliminate 
                        # potential overlaps; so, add compound token only if it does
                        # not overlap with the previous compound token
                        if compound_tokens_lists:
                            last_compound = compound_tokens_lists[-1]
                            if last_compound.start <= spl.start < last_compound.end:
                                add_compound_token = False
                    if add_compound_token:
                        compound_tokens_lists.append(spl)

            # 2.3) Perform hyphenation correction
            if tag_hyphenations:
                if hyphenation_status is None:
                    if last_end==token_span.start and token_span.text == '-':
                        hyphenation_status = '-'
                    else:
                        hyphenation_start = i
                elif hyphenation_status=='-':
                    if last_end==token_span.start:
                        hyphenation_status = 'second'
                    else:
                        hyphenation_status = 'end'
                elif hyphenation_status=='second':
                    if last_end==token_span.start and token_span.text == '-':
                        hyphenation_status = '-'
                    else:
                        hyphenation_status = 'end'
                if hyphenation_status == 'end' and hyphenation_start+1 < i:
                    hyp_start = layers[ self._input_tokens_layer ][hyphenation_start].start
                    hyp_end   = layers[ self._input_tokens_layer ][i-1].end
                    text_snippet = raw_text[hyp_start:hyp_end]
                    if _letter_pattern.search(text_snippet):
                        # The text snippet should contain at least one letter to be 
                        # considered as a potentially hyphenated word; 
                        # This serves to leave out numeric ranges like 
                        #    "15-17.04." or "920-980"
                        spans = layers[ self._input_tokens_layer ][hyphenation_start:i].spans
                        spl = EnvelopingSpan(spans=spans)
                        spl.type = ('hyphenation',)
                        spl.normalized = \
                            self._normalize_word_with_hyphens( text_snippet )
                        compound_tokens_lists.append(spl)
                    hyphenation_status = None
                    hyphenation_start = i
            last_end = token_span.end

        # 3) Apply tagging of 2nd level tokenization hints
        #    (join 1st level compound tokens + regular tokens, if needed)
        if self._tokenization_hints_tagger_2:
            compound_tokens_lists = \
                self._apply_2nd_level_compounding(raw_text, layers, compound_tokens_lists)

        # *) Finally: create a new layer and add spans to the layer
        layer = Layer(name=self.output_layer,
                      enveloping=self._input_tokens_layer,
                      attributes=self.output_attributes,
                      ambiguous=True)
        for spl in compound_tokens_lists:
            layer.add_span(spl)

        resolve_conflicts(layer,
                          conflict_resolving_strategy=self._conflict_resolving_strategy,
                          priority_attribute=None,
                          keep_equal=False)

        def decorator(span, raw_text):
            return {name: getattr(span[0], name) for name in self.output_attributes}
        disamb_tagger = DisambiguatingTagger(output_layer=self.output_layer,
                                             input_layer=self.output_layer,
                                             output_attributes=self.output_attributes,
                                             decorator=decorator)
        temp_layers = layers.copy()
        temp_layers[self.output_layer] = layer
        layer = disamb_tagger.make_layer(raw_text, temp_layers, status)

        return layer


    @staticmethod
    def _load_ignore_words_from_csv(file:str):
        """ Loads words from csv file, and returns as a set.
            Returns an empty set if the file contains no data.
        """
        try:
            df = read_csv(file, na_filter=False, header=None)
            return set(df[0])
        except EmptyDataError:
            return set()

    def _try_to_add_custom_abbreviation( self, raw_text: str, layers, token_id: int, compound_tokens_lists: list):
        """ Checks if a custom non-ending abbreviation starts
            from the position token_id, and if this is True, then 
            checks if conditions for adding the abbreviation are 
            correct ( and  resolves  overlaps with previous compound 
            tokens), and, finally, if conditions are good, appends 
            the detected custom abbreviation at the end of 
            compound_tokens_lists;
        """
        token = layers[ self._input_tokens_layer ][token_id].text
        custom_abbreviation_found = False
        for abbreviation in self.custom_abbreviations:
            if abbreviation == token:
                custom_abbreviation_found = True
                break
        if custom_abbreviation_found:
            # construct potential span
            next_token = ''
            if token_id+1 < len(layers[ self._input_tokens_layer ])-1:
                next_token = layers[ self._input_tokens_layer ][token_id+1].text
            if next_token == '.':
                spans = layers[ self._input_tokens_layer ][token_id:token_id+2]
                normalized = token+next_token
            else:
                spans = layers[ self._input_tokens_layer ][token_id:token_id+1]
                normalized = None
            spl = EnvelopingSpan(spans=spans)
            spl.type = ('non_ending_abbreviation',)
            spl.normalized = normalized
            # before adding: check that none of the disallowed separator 
            # strings are in the middle of the string 
            if self.do_not_join_on_strings:
                discard = False
                for separator in self.do_not_join_on_strings:
                    if separator in raw_text[spl.start:spl.end]:
                       discard = True
                if discard:
                    # Return empty-handed
                    return
            # before adding: check the last compound token:
            add_custom_abbrev = True
            if compound_tokens_lists:
                last_compound = compound_tokens_lists[-1]
                last_type = last_compound.type
                if last_compound.start < spl.start and \
                    spl.end < last_compound.end:
                    # custom compound token is inside 
                    # already existing compound token:
                    # discard it
                    # (the existing compound token already
                    #  fixes potential sentence tokenization
                    #  problems)
                    add_custom_abbrev = False
                elif last_compound.end == spl.end and \
                     'non_ending_abbreviation' not in last_type:
                    # if the last compound token ends
                    # exactly where custom token ends, and
                    # the last compound token is not 
                    # 'non_ending_abbreviation', then
                    # remove the last token to give priority
                    # to the custom 'non_ending_abbreviation'
                    del compound_tokens_lists[-1]
                elif last_compound.end == spl.end and \
                     'non_ending_abbreviation' in last_type:
                    # if the last is already a 'non_ending_abbreviation',
                    # then discard the custom compound
                    # (the existing compound token already
                    #  fixes potential sentence tokenization
                    #  problems)
                    add_custom_abbrev = False
            if add_custom_abbrev:
                compound_tokens_lists.append(spl)

    def _normalize_word_with_hyphens( self, word_text:str ):
        """ Attempts to normalize given word with hyphens.
            Returns the normalized word, or 
                    None, if 1) the word appears in the list of words that should 
                                be ignored;
                             2) the word needs no hyphen-normalization, that is, 
                                it has the same form with and without the hyphen;
        """
        if hasattr(self, 'ignored_words'):
            # If the word with hyphens is inside the list of ignorable words, discard it
            if word_text in self.ignored_words:
                return None
        token = MorphAnalyzedToken( word_text )
        if token is token.normal:
            # If the normalized form of the token is same as the unnormalized form, 
            # return None
            return None
        # Return normalized form of the token
        return token.normal.text

    def _apply_2nd_level_compounding(self, raw_text, layers, compound_tokens_lists:list):
        """ Executes _tokenization_hints_tagger_2 to get hints for 2nd level compounding.

            Performs the 2nd level compounding: joins together regular "tokens" and 
            "compound_tokens" (created by _tokenization_hints_tagger_1) according to the 
            hints.

            And finally, unifies results of the 1st level compounding and the 2nd level 
            compounding into a new compound_tokens_lists.
            Returns updated compound_tokens_lists.
        """
        # Apply regexps to gain 2nd level of tokenization hints
        conflict_status = {}
        new_layer = self._tokenization_hints_tagger_2.make_layer(raw_text,
                                                                 layers,
                                                                 status=conflict_status)
        # Find tokens that should be joined according to 2nd level hints and 
        # create new compound tokens based on them
        for sp in new_layer.span_list:
            # get tokens covered by the span
            covered_compound_tokens =\
                self._get_covered_tokens(
                    sp.start,sp.end,sp.left_strict,sp.right_strict,compound_tokens_lists )
            covered_tokens = \
                self._get_covered_tokens(
                    sp.start, sp.end, sp.left_strict, sp.right_strict, layers[ self._input_tokens_layer ].span_list)
            # remove regular tokens that are within compound tokens
            covered_tokens = \
                self._remove_overlapped_spans(covered_compound_tokens, covered_tokens)
            #print('>>>> ','"'+text.text[sp.start:sp.end]+'"',sp.start,sp.end,sp.pattern_type)
            
            # check the leftmost and the rightmost tokens: 
            #    whether they satisfy the constraints left_strict and right_strict
            constraints_satisfied = True
            leftmost1 = \
                covered_tokens[0].start if covered_tokens else len(raw_text)
            leftmost2 = \
                covered_compound_tokens[0].start if covered_compound_tokens else len(raw_text)
            leftmost = min(leftmost1, leftmost2)
            if sp.left_strict and sp.start != leftmost:
                # hint's left boundary was supposed to match exactly a token start, but did not
                constraints_satisfied = False
            rightmost1 = \
                covered_tokens[-1].end if covered_tokens else -1
            rightmost2 = \
                covered_compound_tokens[-1].end if covered_compound_tokens else -1
            rightmost = max( rightmost1, rightmost2 )
            if sp.right_strict  and  sp.end != rightmost:
                # hint's right boundary was supposed to match exactly a token end, but did not
                constraints_satisfied = False

            # If constraints were satisfied, try to add a new compound token
            if (covered_compound_tokens or covered_tokens) and constraints_satisfied:
                # Create new SpanList
                spl = self._create_new_spanlist(raw_text, layers, covered_compound_tokens, covered_tokens, sp)
                # Check that the new compound token will not contain any of the disallowed strings
                if self.do_not_join_on_strings:
                    discard = False
                    for separator in self.do_not_join_on_strings:
                        if separator in raw_text[spl.start:spl.end]:
                           discard = True
                    if discard:
                        # Cancel creation of the spanlist
                        continue
                # Remove old compound_tokens that are covered with the SpanList
                compound_tokens_lists = \
                    self._remove_overlapped_spans(covered_compound_tokens, compound_tokens_lists)
                # Insert new SpanList into compound_tokens
                self._insert_span(spl, compound_tokens_lists)
                #print('>2>',[text.text[t.start:t.end] for t in spl.spans] )

        return compound_tokens_lists

    def _get_covered_tokens(self, start: int, end: int, left_strict: bool, right_strict: bool, spans: list):
        """
        Filters the list spans and returns a sublist containing spans within 
        the range (start, end).
        
        Parameters left_strict and right_strict can be used to loosen the range
        constraints; e.g. if left_strict==False, then returned spans can start 
        before the given start position.
        """
        covered = []
        if spans:
            for span in spans:
                #print('>>>> ',text.text[span.start:span.end],span.start,span.end, start, end)
                if not left_strict and right_strict:
                    if start <= span.end and span.end <= end:
                        # span's end falls into target's start and end
                        covered.append( span )
                elif left_strict and not right_strict:
                    if start <= span.start and span.start <= end:
                        # span's start falls into target's start and end
                        covered.append( span )
                elif left_strict and right_strict:
                    if start <= span.start and span.end <= end:
                        # span entirely falls into target's start and end
                        covered.append( span )
        return covered

    def _remove_overlapped_spans(self, compound_token_spans:list, regular_spans:list):
        """
        Filters the list regular_spans and removes spans  that  are  entirely
        contained within compound_token_spans.
        Returns a new list containing filtered regular_spans.
        """
        filtered = []
        for regular_span in regular_spans:
            is_entirely_overlapped = False
            for compound_token_span in compound_token_spans:
                if compound_token_span.start <= regular_span.start and \
                   regular_span.end <= compound_token_span.end:
                   is_entirely_overlapped = True
                   break
            if not is_entirely_overlapped:
                filtered.append(regular_span)
        return filtered

    def _insert_span(self, span:Union['Span', SpanList], spans:list, discard_duplicate:bool=False):
        """
        Inserts given span into spans so that the list remains sorted
        ascendingly according to text positions.
        
        If discard_duplicate==True, then span is only inserted iff 
        the same span does not exist in the list; By default, duplicates
        are allowed (discard_duplicate=False);
        """
        i = 0
        inserted = False
        is_duplicate = False
        while i < len(spans):
            if span.start == spans[i].start and \
               span.end == spans[i].end:
               is_duplicate = True
            if span.end <= spans[i].start:
                if not discard_duplicate or (discard_duplicate and not is_duplicate):
                    spans.insert(i, span)
                    inserted = True
                    break
            i += 1
        if not inserted:
            if not discard_duplicate or (discard_duplicate and not is_duplicate):
                spans.append(span)


    def _create_new_spanlist(self, raw_text, layers, compound_token_spans:list, regular_spans:list, joining_span:SpanList):
        """
        Creates new SpanList that covers both compound_token_spans and regular_spans from given 
        text. Returns created SpanList.
        """
        # 1) Get all tokens covered by compound_token_spans and regular_spans
        #    (basis material for the new spanlist)
        #    (also, leave out duplicate spans, if such exist)
        all_covered_tokens = []
        for compound_token_spanlist in compound_token_spans:
            for span in compound_token_spanlist:
                self._insert_span(span, all_covered_tokens, discard_duplicate=True)
        for span in regular_spans:
            self._insert_span(span, all_covered_tokens, discard_duplicate=True)

        # 2) Get attributes
        all_normalizations = {}
        all_types = []
        for compound_token_spanlist in compound_token_spans:
            span_start = compound_token_spanlist.start
            span_end   = compound_token_spanlist.end
            if compound_token_spanlist.normalized:   # if normalization != None
                all_normalizations[span_start] = ( compound_token_spanlist.normalized, \
                                                   span_end )
            for compound_token_type in compound_token_spanlist.type:
                all_types.append( compound_token_type )
        # Add type of the joining span (if it exists) to the end
        joining_span_type = joining_span.pattern_type if hasattr(joining_span, 'pattern_type') else None
        if joining_span_type:
            all_types.append(joining_span_type)

        # 3) Provide normalized string, if normalization is required
        if hasattr(joining_span, 'normalized') and joining_span.normalized:
            start = joining_span.start
            all_normalizations[start] = (joining_span.normalized, joining_span.end)
        normalized_str = None
        if len(all_normalizations.keys()) > 0:
            # get start and end of the entire string (unnormalized)
            start_index = all_covered_tokens[0].start
            end_index   = all_covered_tokens[-1].end
            # reconstruct string with normalizations
            i = start_index
            normalized = []
            last_was_single_symbol = True
            while i < end_index:
                # check if we have already added a normalization, and its 
                # last character overlaps with a beginning of a new/starting 
                # normalization;
                #    ==> if so, then merge two normalizations ...
                if i-1 in all_normalizations and len(normalized)>0 and \
                   not last_was_single_symbol:
                    character = raw_text[i-1:i]
                    # check if the characters match
                    if normalized[-1].endswith(character) and \
                        all_normalizations[i-1][0].startswith(character):
                        # if one normalization ends where another starts, 
                        # then merge two normalizations
                        merged_norm = all_normalizations[i-1][0]
                        merged_norm = merged_norm[1:] # skip the overlapping character
                        normalized.append( merged_norm )
                        i = all_normalizations[i-1][1]
                        last_was_single_symbol = False
                        continue
                # check if a new normalization starts from the current position
                if i in all_normalizations:
                    # add normalized string
                    normalized.append(all_normalizations[i][0])
                    # move to the next position
                    i = all_normalizations[i][1]
                    last_was_single_symbol = False
                else:
                    # finally, add a single symbol, if no normalization was found
                    normalized.append(raw_text[i:i+1])
                    last_was_single_symbol = True
                    i += 1
            normalized_str = ''.join(normalized)
        
        # 4) Create new SpanList and assign attributes
        spans = all_covered_tokens
        spl = EnvelopingSpan(spans=spans)
        spl.type = ('tokenization_hint',)
        spl.normalized = normalized_str
        if all_types:
            # Few "repairs" on the types:
            # 1) "non_ending_abbreviation" ('st') + "case_ending" ('st')
            #     ==> "case_ending" ('st')
            if "non_ending_abbreviation" in all_types and \
               "case_ending" in all_types:
                start_index = all_covered_tokens[0].start
                end_index   = all_covered_tokens[-1].end
                full_string = raw_text[start_index : end_index]
                if full_string.endswith('st'):
                    # 'st' is not "non_ending_abbreviation", but
                    #  case ending instead
                    all_types.remove("non_ending_abbreviation")
            # 2) "sign" ('-') + "hyphenation" ('-')
            #     ==> "hyphenation" ('-')
            if "sign" in all_types and \
               "hyphenation" in all_types:
                start_index = all_covered_tokens[0].start
                end_index   = all_covered_tokens[-1].end
                full_string = raw_text[start_index : end_index]
                if _letter_pattern.match(full_string[0]):
                    # if the string begins with a letter instead of 
                    # the sign, remove the sign type
                    all_types.remove("sign")
            spl.type = ()
            for type in all_types:
                spl.type += (type,)

        return spl
