import pytest
from django.http import HttpResponseRedirect
from tri_struct import Struct

from iommi.admin import Admin
from iommi.base import values
from tests.helpers import req
from tests.models import Foo


@pytest.mark.django_db
def test_bulk_edit_for_non_unique():
    request = req('get')
    request.user = Struct(is_staff=True, is_authenticated=True)
    p = Admin.list(request=request, app_name='tests', model_name='adminunique')
    p = p.bind(request=request)
    assert [x._name for x in values(p.parts.list_tests_adminunique.columns) if x.bulk.include] == ['foo']


@pytest.mark.django_db
def test_all_models():
    request = req('get')
    request.user = Struct(is_staff=True, is_authenticated=True)
    p = Admin.all_models(request=request)
    p = p.bind(request=request)
    assert list(p.parts.all_models.columns.keys()) == ['app_name', 'model_name']


@pytest.mark.django_db
def test_create():
    request = req('get')
    request.user = Struct(is_staff=True, is_authenticated=True)
    c = Admin.create(request=request, app_name='tests', model_name='foo')
    p = c.bind(request=request)
    assert list(p.parts.create_tests_foo.fields.keys()) == ['foo']

    assert Foo.objects.count() == 0
    p = c.bind(request=req('post', foo=7, **{'-submit': ''}))
    assert p.parts.create_tests_foo.is_valid()
    p.render_to_response()
    assert Foo.objects.count() == 1
    assert Foo.objects.get().foo == 7


@pytest.mark.django_db
def test_edit():
    request = req('get')
    request.user = Struct(is_staff=True, is_authenticated=True)
    assert Foo.objects.count() == 0
    f = Foo.objects.create(foo=7)

    c = Admin.edit(request=request, app_name='tests', model_name='foo', pk=f.pk)
    p = c.bind(request=req('post', foo=11, **{'-submit': ''}))
    assert p.parts.edit_tests_foo.is_valid()
    p.render_to_response()
    assert Foo.objects.get().foo == 11


@pytest.mark.django_db
def test_delete():
    request = req('get')
    request.user = Struct(is_staff=True, is_authenticated=True)
    assert Foo.objects.count() == 0
    f = Foo.objects.create(foo=7)

    c = Admin.delete(request=request, app_name='tests', model_name='foo', pk=f.pk)
    p = c.bind(request=req('post', **{'-submit': ''}))
    assert p.parts.delete_tests_foo.is_valid()
    p.render_to_response()
    assert Foo.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize('is_authenticated', [True, False])
@pytest.mark.parametrize('view,kwargs', [
    (Admin.all_models, dict()),
    (Admin.list, dict(app_name='tests', model_name='foo')),
    (Admin.edit, dict(app_name='tests', model_name='foo', pk=0)),
    (Admin.delete, dict(app_name='tests', model_name='foo', pk=0)),
])
def test_redirect_to_login(settings, is_authenticated, view, kwargs):
    settings.ROOT_URLCONF = Admin.urls()
    if 'pk' in kwargs:
        Foo.objects.create(pk=kwargs['pk'], foo=1)
    request = req('get')
    request.user = Struct(is_staff=True, is_authenticated=is_authenticated)

    result = view(request=request, **kwargs)

    if not is_authenticated:
        assert isinstance(result, HttpResponseRedirect)
        assert result.url == '/login/?next=%2F'
    else:
        assert isinstance(result, Admin)
