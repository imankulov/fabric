from __future__ import with_statement

import sys
import copy
from contextlib import contextmanager

from fudge import Fake
from nose.tools import ok_, eq_, raises

from fabric.decorators import hosts, roles, task
from fabric.main import (get_hosts, parse_arguments, _merge, _escape_split,
        load_fabfile)

import fabric.state
from fabric.state import _AttributeDict

from utils import mock_streams, patched_env
import os
import sys


#
# Basic CLI stuff
#

def test_argument_parsing():
    for args, output in [
        # Basic 
        ('abc', ('abc', [], {}, [], [], [])),
        # Arg
        ('ab:c', ('ab', ['c'], {}, [], [], [])),
        # Kwarg
        ('a:b=c', ('a', [], {'b':'c'}, [], [], [])),
        # Arg and kwarg
        ('a:b=c,d', ('a', ['d'], {'b':'c'}, [], [], [])),
        # Multiple kwargs
        ('a:b=c,d=e', ('a', [], {'b':'c','d':'e'}, [], [], [])),
        # Host
        ('abc:host=foo', ('abc', [], {}, ['foo'], [], [])),
        # Hosts with single host
        ('abc:hosts=foo', ('abc', [], {}, ['foo'], [], [])),
        # Hosts with multiple hosts
        # Note: in a real shell, one would need to quote or escape "foo;bar".
        # But in pure-Python that would get interpreted literally, so we don't.
        ('abc:hosts=foo;bar', ('abc', [], {}, ['foo', 'bar'], [], [])),

        # Exclude hosts
        ('abc:hosts=foo;bar,exclude_hosts=foo', ('abc', [], {}, ['foo', 'bar'], [], ['foo'])),
        ('abc:hosts=foo;bar,exclude_hosts=foo;bar', ('abc', [], {}, ['foo', 'bar'], [], ['foo','bar'])),
       # Empty string args
        ("task:x=y,z=", ('task', [], {'x': 'y', 'z': ''}, [], [], [])),
        ("task:foo,,x=y", ('task', ['foo', ''], {'x': 'y'}, [], [], [])),
    ]:
        yield eq_, parse_arguments([args]), [output]


def test_escaped_task_arg_split():
    """
    Allow backslashes to escape the task argument separator character
    """
    argstr = r"foo,bar\,biz\,baz,what comes after baz?"
    eq_(
        _escape_split(',', argstr),
        ['foo', 'bar,biz,baz', 'what comes after baz?']
    )


#
# Host/role decorators
#

def eq_hosts(command, host_list):
    eq_(set(get_hosts(command, [], [], [])), set(host_list))

def test_hosts_decorator_by_itself():
    """
    Use of @hosts only
    """
    host_list = ['a', 'b']

    @hosts(*host_list)
    def command():
        pass

    eq_hosts(command, host_list)


fake_roles = {
    'r1': ['a', 'b'],
    'r2': ['b', 'c']
}

@patched_env({'roledefs': fake_roles})
def test_roles_decorator_by_itself():
    """
    Use of @roles only
    """
    @roles('r1')
    def command():
        pass
    eq_hosts(command, ['a', 'b'])


@patched_env({'roledefs': fake_roles})
def test_hosts_and_roles_together():
    """
    Use of @roles and @hosts together results in union of both
    """
    @roles('r1', 'r2')
    @hosts('a')
    def command():
        pass
    eq_hosts(command, ['a', 'b', 'c'])

tuple_roles = {
    'r1': ('a', 'b'),
    'r2': ('b', 'c'),
}


@patched_env({'roledefs': tuple_roles})
def test_roles_as_tuples():
    """
    Test that a list of roles as a tuple succeeds
    """
    @roles('r1')
    def command():
        pass
    eq_hosts(command, ['a', 'b'])


@patched_env({'hosts': ('foo', 'bar')})
def test_hosts_as_tuples():
    """
    Test that a list of hosts as a tuple succeeds
    """
    def command():
        pass
    eq_hosts(command, ['foo', 'bar'])


@patched_env({'hosts': ['foo']})
def test_hosts_decorator_overrides_env_hosts():
    """
    If @hosts is used it replaces any env.hosts value
    """
    @hosts('bar')
    def command():
        pass
    eq_hosts(command, ['bar'])
    assert 'foo' not in get_hosts(command, [], [], [])

@patched_env({'hosts': ['foo']})
def test_hosts_decorator_overrides_env_hosts_with_task_decorator_first():
    """
    If @hosts is used it replaces any env.hosts value even with @task
    """
    @task
    @hosts('bar')
    def command():
        pass
    eq_hosts(command, ['bar'])
    assert 'foo' not in get_hosts(command, [], [])

@patched_env({'hosts': ['foo']})
def test_hosts_decorator_overrides_env_hosts_with_task_decorator_last():
    @hosts('bar')
    @task
    def command():
        pass
    eq_hosts(command, ['bar'])
    assert 'foo' not in get_hosts(command, [], [])


@patched_env({'hosts': [' foo ', 'bar '], 'roles': [],
        'exclude_hosts':[]})
def test_hosts_stripped_env_hosts():
    """
    Make sure hosts defined in env.hosts are cleaned of extra spaces
    """
    def command():
        pass
    eq_hosts(command, ['foo', 'bar'])


spaced_roles = {
    'r1': [' a ', ' b '],
    'r2': ['b', 'c'],
}

@patched_env({'roledefs': spaced_roles})
def test_roles_stripped_env_hosts():
    """
    Make sure hosts defined in env.roles are cleaned of extra spaces
    """
    @roles('r1')
    def command():
        pass
    eq_hosts(command, ['a', 'b'])


def test_hosts_decorator_expands_single_iterable():
    """
    @hosts(iterable) should behave like @hosts(*iterable)
    """
    host_list = ['foo', 'bar']

    @hosts(host_list)
    def command():
        pass

    eq_(command.hosts, host_list)

def test_roles_decorator_expands_single_iterable():
    """
    @roles(iterable) should behave like @roles(*iterable)
    """
    role_list = ['foo', 'bar']

    @roles(role_list)
    def command():
        pass

    eq_(command.roles, role_list)


#
# Basic role behavior
#

@patched_env({'roledefs': fake_roles})
@raises(SystemExit)
@mock_streams('stderr')
def test_aborts_on_nonexistent_roles():
    """
    Aborts if any given roles aren't found
    """
    _merge([], ['badrole'])


lazy_role = {'r1': lambda: ['a', 'b']}

@patched_env({'roledefs': lazy_role})
def test_lazy_roles():
    """
    Roles may be callables returning lists, as well as regular lists
    """
    @roles('r1')
    def command():
        pass
    eq_hosts(command, ['a', 'b'])


#
# Fabfile loading
#

def run_load_fabfile(path, sys_path):
    # Module-esque object
    fake_module = Fake().has_attr(__dict__={})
    # Fake __import__
    importer = Fake(callable=True).returns(fake_module)
    # Snapshot sys.path for restore
    orig_path = copy.copy(sys.path)
    # Update with fake path
    sys.path = sys_path
    # Test for side effects
    load_fabfile(path, importer=importer)
    eq_(sys.path, sys_path)
    # Restore
    sys.path = orig_path

def test_load_fabfile_should_not_remove_real_path_elements():
    for fabfile_path, sys_dot_path in (
        # Directory not in path
        ('subdir/fabfile.py', ['not_subdir']),
        ('fabfile.py', ['nope']),
        # Directory in path, but not at front
        ('subdir/fabfile.py', ['not_subdir', 'subdir']),
        ('fabfile.py', ['not_subdir', '']),
        ('fabfile.py', ['not_subdir', '', 'also_not_subdir']),
        # Directory in path, and at front already
        ('subdir/fabfile.py', ['subdir']),
        ('subdir/fabfile.py', ['subdir', 'not_subdir']),
        ('fabfile.py', ['', 'some_dir', 'some_other_dir']),
    ):
            yield run_load_fabfile, fabfile_path, sys_dot_path


#
# Namespacing and new-style tasks
#

def fabfile(name):
    return os.path.join(os.path.dirname(__file__), 'support', name)

@contextmanager
def path_prefix(module):
    i = 0
    sys.path.insert(i, os.path.dirname(module))
    yield
    sys.path.pop(i)


def test_implicit_discovery():
    """
    Default to automatically collecting all tasks in a fabfile module
    """
    implicit = fabfile("implicit_fabfile.py")
    with path_prefix(implicit):
        docs, funcs = load_fabfile(implicit)
        ok_(len(funcs) == 2)
        ok_("foo" in funcs)
        ok_("bar" in funcs)


def test_explicit_discovery():
    """
    If __all__ is present, only collect the tasks it specifies
    """
    explicit = fabfile("explicit_fabfile.py")
    with path_prefix(explicit):
        docs, funcs = load_fabfile(explicit)
        ok_(len(funcs) == 1)
        ok_("foo" in funcs)
        ok_("bar" not in funcs)


def test_should_load_decorated_tasks_only_if_one_is_found():
    """
    If any new-style tasks are found, *only* new-style tasks should load
    """
    module = fabfile('decorated_fabfile.py')
    with path_prefix(module):
        docs, funcs = load_fabfile(module)
        eq_(1, len(funcs))
        ok_('foo' in funcs)


def test_class_based_tasks_are_found_with_proper_name():
    """
    Wrapped new-style tasks should preserve their function names
    """
    module = fabfile('decorated_fabfile_with_classbased_task.py')
    with path_prefix(module):
        docs, funcs = load_fabfile(module)
        eq_(1, len(funcs))
        ok_('foo' in funcs)


def test_recursion_steps_into_nontask_modules():
    """
    Recursive loading will continue through modules with no tasks
    """
    module = fabfile('deep')
    with path_prefix(module):
        docs, funcs = load_fabfile(module)
        eq_(len(funcs), 1)
        ok_('submodule.subsubmodule.deeptask' in funcs)


def test_newstyle_task_presence_skips_classic_task_modules():
    """
    Classic-task-only modules shouldn't add tasks if any new-style tasks exist
    """
    module = fabfile('deep')
    with path_prefix(module):
        docs, funcs = load_fabfile(module)
        eq_(len(funcs), 1)
        ok_('submodule.classic_task' not in funcs)
