from __future__ import unicode_literals, absolute_import

from datetime import date
from django.db.models import Q, F
from django.core.exceptions import ObjectDoesNotExist
import operator
from pyparsing import CaselessLiteral, Word, delimitedList, Optional, Combine, Group, alphas, nums, alphanums, Forward, oneOf, quotedString, ZeroOrMore, Keyword, ParseResults, ParseException
from tri.struct import Frozen, merged, Struct
from tri.declarative import declarative, creation_ordered, extract_subkeys, setdefaults
from tri.named_struct import NamedStruct, NamedStructField
import tri.form

# TODO: short form for boolean values? "is_us_person" or "!is_us_person"

__version__ = '1.3.0'


class QueryException(Exception):
    pass


PRECEDENCE = {
    'and': 3,
    'or': 2,
}

Q_OP_BY_OP = {
    '>': 'gt',
    '=>': 'gte',
    '>=': 'gte',
    '<': 'lt',
    '<=': 'lte',
    '=<': 'lte',
    '=': 'iexact',
    ':': 'icontains',
}

FREETEXT_SEARCH_NAME = 'term'
ADVANCED_QUERY_PARAM = 'query'


def request_data(request):
    if request.method == 'POST':
        return request.POST
    elif request.method == 'GET':
        return request.GET
    else:
        assert False, "unknown request method %s" % request.method  # pragma: no cover


def value_to_query_string_value_string(v):
    if type(v) == bool:
        return {True: '1', False: '0'}.get(v)
    if type(v) in (int, float, long):
        return str(v)
    return '"%s"' % v


def default_value_to_q(variable, op, value_string_or_f):
    if variable.attr is None:
        return Q()
    negated = False
    if op in ('!=', '!:'):
        negated = True
        op = op[1:]
    if isinstance(value_string_or_f, basestring) and value_string_or_f.lower() == 'null':
        r = Q(**{variable.attr: None})
    else:
        r = Q(**{variable.attr + '__' + variable.op_to_q_op(op): value_string_or_f})
    if negated:
        return ~r
    else:
        return r


MISSING = object()


class VariableBase(NamedStruct):
    name = NamedStructField()
    attr = NamedStructField(default=MISSING)
    gui = NamedStructField()

    gui_op = NamedStructField(default='=')
    op_to_q_op = NamedStructField(default=lambda op: Q_OP_BY_OP[op])
    value_to_q = NamedStructField(default=default_value_to_q)
    freetext = NamedStructField()

    model = NamedStructField()
    choices = NamedStructField()
    value_to_q_lookup = NamedStructField()


@creation_ordered
class Variable(Frozen, VariableBase):
    """
    Class that describes a variable that you can search for.
    """
    def __init__(self, **kwargs):
        """
        Parameters with the prefix "gui__" will be passed along downstream to the tri.form.Field instance if applicable. This can be used to tweak the basic style interface.

        :param gui__show: set to True to display a GUI element for this variable in the basic style interface.
        :param gui__class: the factory to create a tri.form.Field for the basic GUI, for example tri.form.Field.choice. Default: tri.form.Field
        """
        name = kwargs.get('name')
        if name:
            if kwargs.get('attr') is MISSING:
                kwargs['attr'] = name

        setdefaults(kwargs, dict(
            gui=Struct(extract_subkeys(kwargs, 'gui', defaults={
                'show': False,
                'class': tri.form.Field,
                'required': False
            })),
        ))
        kwargs = {k: v for k, v in kwargs.items() if not k.startswith('gui__')}

        super(Variable, self).__init__(**kwargs)

    @staticmethod
    def text(**kwargs):  # pragma: no cover
        return Variable(**kwargs)

    @staticmethod
    def case_sensitive(**kwargs):
        """
        Case sensitive text field.
        """
        return Variable(op_to_q_op=lambda op: {'=': 'exact', ':': 'contains'}.get(op) or Q_OP_BY_OP[op], **kwargs)

    @staticmethod
    def choice(**kwargs):  # pragma: no cover
        """
        Field that has one value out of a set.
        :type choices: list
        """
        setdefaults(kwargs, dict(
            gui__choices=kwargs.get('choices'),
            gui__class=tri.form.Field.choice,
        ))
        return Variable(**kwargs)

    @staticmethod
    def choice_queryset(**kwargs):
        """
        Field that has one value out of a set.
        :type choices: django.db.models.QuerySet
        """
        def choice_queryset_value_to_q(variable, op, value_string_or_f):
            assert op == '='
            if variable.attr is None:
                return Q()
            try:
                instance = variable.model.objects.get(**{variable.value_to_q_lookup: value_string_or_f})
            except ObjectDoesNotExist:
                return None
            return Q(**{variable.attr + '__pk': instance.pk})

        setdefaults(kwargs, dict(
            gui__class=tri.form.Field.choice_queryset,
            gui__choices=kwargs['choices'],
            gui__model=kwargs['model'],
            op_to_q_op=lambda op: 'exact',
            value_to_q_lookup='name',
            value_to_q=choice_queryset_value_to_q,
        ))
        return Variable(**kwargs)

    @staticmethod
    def boolean(**kwargs):  # pragma: no cover
        """
        Boolean field. Tries hard to parse a boolean value from its input.
        """
        def boolean_value_to_q(variable, op, value_string_or_f):
            if isinstance(value_string_or_f, basestring):
                value_string_or_f = tri.form.bool_parse(value_string_or_f)
            return default_value_to_q(variable, op, value_string_or_f)

        setdefaults(kwargs, dict(
            gui__class=tri.form.Field.boolean,
            value_to_q=boolean_value_to_q,
        ))
        return Variable(**kwargs)

    @staticmethod
    def integer(**kwargs):  # pragma: no cover
        """
        Boolean field. Tries hard to parse a boolean value from its input.
        """
        setdefaults(kwargs, dict(
            gui__class=tri.form.Field.integer,
        ))
        return Variable(**kwargs)


class StringValue(unicode):
    def __new__(cls, s):
        if s.startswith('"') and s.endswith('"'):
            s = s[1:-1]
        return super(StringValue, cls).__new__(cls, s)


@declarative(Variable, 'variables')
class Query(object):
    """
    Declare a query language. Example:

    .. code:: python

        class CarQuery(Query):
            make = Variable.choice(choices=['Toyota', 'Volvo', 'Ford])
            model = Variable()

        query_set = CarQuery().request_to_q(request)
    """
    variables = []
    """:type: list of Variable"""
    variable_by_name = {}

    def __init__(self, variables=None):  # variables=None to make pycharm tooling not confused
        """
        :type variables: list of Variable
        """
        assert variables is not None
        if isinstance(variables, dict):  # Declarative case
            self.variables = [merged(variable, name=name) for name, variable in variables.items()]
        else:
            self.variables = variables

        self.variable_by_name = {variable.name: variable for variable in self.variables}

    def parse(self, query_string):
        """
        :type query_string: str | unicode
        :rtype: Q
        """
        query_string = query_string.strip()
        if not query_string:
            return Q()
        parser = self._grammar()
        try:
            tokens = parser.parseString(query_string, parseAll=True)
        except ParseException:
            raise QueryException('Invalid syntax for query')
        return self.compile(tokens)

    def compile(self, tokens):
        """
        :rtype: Q
        """
        items = []
        for token in tokens:
            if isinstance(token, ParseResults):
                items.append(self.compile(token))
            elif isinstance(token, Q):
                items.append(token)
            elif token in ('and', 'or'):
                items.append(token)
        return self.__rpn_to_q(self.__to_rpn(items))

    @staticmethod
    def __rpn_to_q(tokens):
        stack = []
        for each in tokens:
            if isinstance(each, Q):
                stack.append(each)
            else:
                op = each
                # infix right hand operator is on the top of the stack
                right, left = stack.pop(), stack.pop()
                stack.append(left & right if op == 'and' else left | right)
        assert len(stack) == 1
        return stack[0]

    @staticmethod
    def __to_rpn(tokens):
        # Convert a infix sequence of Q objects and 'and/or' operators using
        # dijkstra shunting yard algorithm into RPN
        if len(tokens) == 1:
            return tokens
        result_q, stack = [], []
        for token in tokens:
            assert token is not None
            if isinstance(token, Q):
                result_q.append(token)
            elif token in PRECEDENCE:
                p1 = PRECEDENCE[token]
                while stack:
                    t2, p2 = stack[-1]
                    if p1 <= p2:
                        stack.pop()
                        result_q.append(t2)
                    else:  # pragma: no cover
                        break
                stack.append((token, PRECEDENCE[token]))
        while stack:
            result_q.append(stack.pop()[0])
        return result_q

    def _grammar(self):
        """
        Pyparsing implementation of a where clause grammar based on http://pyparsing.wikispaces.com/file/view/simpleSQL.py

        The query language is a series of statements separated by AND or OR operators and parentheses can be used to group/provide
        precedence.

        A statement is a combination of three strings "<variable> <operator> <value>" or "<value> <operator> <variable>".

        A value can be a string, integer or a real(floating) number, a (ISO YYYY-MM-DD) date, or a year based period (nY) where the resulting value
        will typically be base_date + relativedelta(years=n).
        See self.period_to_date

        An operator must be one of "= != < > >= <= !:" and are translated into django __lte or equivalent suffixes.
        See self.as_q

        Example
        something < 10 AND other >= 2015-01-01 AND (foo < 1 OR bar > 1)

        """
        quoted_string_excluding_quotes = quotedString.copy().setParseAction(lambda token: StringValue(token[0]))
        and_ = Keyword('and', caseless=True)
        or_ = Keyword('or', caseless=True)
        exponential_marker = CaselessLiteral('E')
        binary_op = oneOf('=> =< = < > >= <= : != !:', caseless=True).setResultsName('operator')
        arith_sign = Word('+-', exact=1)
        integer = Word(nums)

        real_num = Combine(Optional(arith_sign) + (integer + '.' + Optional(integer) | ('.' + integer)) + Optional(exponential_marker + Optional(arith_sign) + integer)).setParseAction(lambda t: float(t[0]))
        int_num = Combine(Optional(arith_sign) + integer + Optional(exponential_marker + Optional('+') + integer)).setParseAction(lambda t: int(t[0]))

        def parse_date_str(token):
            y, _, m, _, d = token
            return date(*map(int, (y, m, d)))
        date_str = (integer('year') + '-' + integer('month') + '-' + integer('day')).setParseAction(parse_date_str)

        # define query tokens
        identifier = Word(alphas, alphanums + '_$-').setName('identifier')
        variable_name = delimitedList(identifier, '.', combine=True)
        value_string = date_str | real_num | int_num | variable_name | quoted_string_excluding_quotes

        # Define a where expression
        where_expression = Forward()
        binary_operator_statement = (variable_name + binary_op + value_string).setParseAction(self.binary_op_as_q)
        free_text_statement = quotedString.copy().setParseAction(self.freetext_as_q)
        operator_statement = binary_operator_statement | free_text_statement
        where_condition = Group(operator_statement | ('(' + where_expression + ')'))
        where_expression << where_condition + ZeroOrMore((and_ | or_) + where_expression)

        # define the full grammar
        query_statement = Forward()
        query_statement << Group(where_expression).setResultsName("where")
        return query_statement

    def binary_op_as_q(self, token):
        """
        Convert a parsed token of variable_name OPERATOR variable_name into a Q object
        """
        variable_name, op, value_string_or_variable_name = token
        variable = self.variable_by_name.get(variable_name.lower())
        if variable:
            if isinstance(value_string_or_variable_name, basestring) and not isinstance(value_string_or_variable_name, StringValue) and value_string_or_variable_name.lower() in self.variable_by_name:
                value_string_or_f = F(self.variable_by_name[value_string_or_variable_name.lower()].attr)
            else:
                value_string_or_f = value_string_or_variable_name
            result = variable.value_to_q(variable=variable, op=op, value_string_or_f=value_string_or_f)
            if result is None:
                raise QueryException('Unknown value "%s" for variable "%s"' % (value_string_or_f, variable.name))
            return result
        raise QueryException('Unknown variable "%s"' % variable_name)

    def freetext_as_q(self, token):
        assert any(v.freetext for v in self.variables)
        assert len(token) == 1
        token = token[0].strip('"')

        return reduce(operator.or_, [Q(**{variable.attr + '__' + variable.op_to_q_op(':'): token})
                                     for variable in self.variables
                                     if variable.freetext])

    def form(self, request):
        """
        Create a form and validate input based on a request.
        """
        fields = []

        if any(v.freetext for v in self.variables):
            fields.append(tri.form.Field(name=FREETEXT_SEARCH_NAME, label='Search', required=False))

        for variable in self.variables:
            if variable.gui.show:
                # pass gui__* parameters to the GUI component
                params = merged(variable.gui, name=variable.name)
                fields.append(params.pop('class')(**params))

        form = tri.form.Form(request=request, fields=fields)
        form.request = request
        form.tri_query = self
        form.tri_query_advanced_value = request_data(request).get(ADVANCED_QUERY_PARAM, '')
        return form

    def request_to_query_string(self, request):
        """
        Based on the data in the request, return the equivalent query string that you can use with parse() to create a query set.

        :type request: django.http.request.HttpRequest
        """
        form = self.form(request)
        if request_data(request).get(ADVANCED_QUERY_PARAM, '').strip():
            return request_data(request).get(ADVANCED_QUERY_PARAM)
        elif form.is_valid():
            # TODO: handle escaping for cleaned_data, this will blow up if the value contains "
            result = [''.join([field.name,
                               self.variable_by_name[field.name].gui_op,
                               value_to_query_string_value_string(field.value)])
                      for field in form.fields
                      if field.name != FREETEXT_SEARCH_NAME and field.value not in (None, '')]

            if FREETEXT_SEARCH_NAME in form.fields_by_name:
                freetext = form.fields_by_name[FREETEXT_SEARCH_NAME].value
                if freetext:
                    # TODO: handle escaping for freetext, this will blow up if the value contains "
                    result.append('(%s)' % ' or '.join(['%s:"%s"' % (variable.name, freetext)
                                                        for variable in self.variables
                                                        if variable.freetext]))
            return ' and '.join(result)
        else:
            return ''

    def request_to_q(self, request):
        """
        Create a query set based on the data in the request.
        """
        return self.parse(self.request_to_query_string(request))
