from typing import Dict
from typing import Iterator
from typing import List
from typing import Optional
from typing import Tuple

from lxml.etree import QName

from xsdata.codegen.models import Attr
from xsdata.codegen.models import AttrType
from xsdata.codegen.models import Class
from xsdata.codegen.models import Extension
from xsdata.codegen.models import Restrictions
from xsdata.models.enums import DataType
from xsdata.models.enums import Namespace
from xsdata.models.enums import Tag
from xsdata.models.mixins import ElementBase
from xsdata.models.xsd import Attribute
from xsdata.models.xsd import AttributeGroup
from xsdata.models.xsd import ComplexType
from xsdata.models.xsd import Element
from xsdata.models.xsd import Group
from xsdata.models.xsd import Schema
from xsdata.models.xsd import SimpleType
from xsdata.utils import text


class SchemaMapper:
    """Map a schema instance to classes, extensions and attributes."""

    @classmethod
    def map(cls, schema: Schema) -> List[Class]:
        """Map schema children elements to classes."""

        return [
            cls.build_class(element, container, schema.module, schema.target_namespace)
            for container, element in cls.root_elements(schema)
        ]

    @classmethod
    def root_elements(cls, schema: Schema):
        """Return all valid schema elements that can be converted to
        classes."""

        for override in schema.overrides:
            for child in override.children(condition=cls.is_class):
                yield Tag.OVERRIDE, child

        for redefine in schema.redefines:
            for child in redefine.children(condition=cls.is_class):
                yield Tag.REDEFINE, child

        for child in schema.children(condition=cls.is_class):
            yield Tag.SCHEMA, child

    @classmethod
    def build_class(
        cls,
        obj: ElementBase,
        container: str,
        module: str,
        target_namespace: Optional[str],
    ) -> Class:
        """Build and return a class instance."""
        instance = Class(
            qname=QName(target_namespace, obj.real_name),
            abstract=obj.is_abstract,
            namespace=cls.element_namespace(obj, target_namespace),
            mixed=obj.is_mixed,
            nillable=obj.is_nillable,
            type=type(obj),
            container=container,
            help=obj.display_help,
            ns_map=obj.ns_map,
            module=module,
            substitutions=cls.build_substitutions(obj, target_namespace),
        )

        cls.build_class_extensions(obj, instance)
        cls.build_class_attributes(obj, instance)
        return instance

    @classmethod
    def build_substitutions(
        cls, obj: ElementBase, target_namespace: Optional[str]
    ) -> List[QName]:
        return [
            QName(obj.ns_map.get(prefix, target_namespace), suffix)
            for prefix, suffix in map(text.split, obj.substitutions)
        ]

    @classmethod
    def build_class_attributes(cls, obj: ElementBase, target: Class):
        """Build the target class attributes from the given ElementBase
        children."""

        restrictions = Restrictions.from_element(obj)
        for child, restrictions in cls.element_children(obj, restrictions):
            cls.build_class_attribute(target, child, restrictions)

        target.attrs.sort(key=lambda x: x.index)

    @classmethod
    def build_class_extensions(cls, obj: ElementBase, target: Class):
        """Build the item class extensions from the given ElementBase
        children."""
        extensions = {}
        raw_type = obj.raw_type
        if raw_type:
            restrictions = obj.get_restrictions()
            extension = cls.build_class_extension(target, raw_type, 0, restrictions)
            extensions[raw_type] = extension

        for extension in cls.children_extensions(obj, target):
            extensions[extension.type.name] = extension

        target.extensions = sorted(extensions.values(), key=lambda x: x.type.index)

    @classmethod
    def build_data_type(
        cls, target: Class, name: str, index: int = 0, forward: bool = False
    ) -> AttrType:
        """Create an attribute type for the target class."""
        prefix, suffix = text.split(name)
        namespace = target.ns_map.get(prefix, target.qname.namespace)
        native = (
            Namespace.get_enum(namespace) is not None
            and DataType.get_enum(suffix) is not None
        )

        return AttrType(
            qname=QName(namespace, suffix), index=index, native=native, forward=forward,
        )

    @classmethod
    def element_children(
        cls, obj: ElementBase, restrictions: Restrictions
    ) -> Iterator[Tuple[ElementBase, Restrictions]]:
        """Recursively find and return all child elements that are qualified to
        be class attributes."""

        for child in obj.children():
            if child.is_attribute:
                yield child, restrictions
            else:
                yield from cls.element_children(
                    child, restrictions=Restrictions.from_element(child)
                )

    @classmethod
    def element_namespace(
        cls, obj: ElementBase, target_namespace: Optional[str]
    ) -> Optional[str]:
        """
        Return the target namespace for the given schema element.

        In order:
            - elements/attributes with specific target namespace
            - prefixed elements returns the namespace from schema ns_map
            - qualified elements returns the schema target namespace
            - unqualified elements return an empty string
            - unqualified attributes return None
        """

        raw_namespace = obj.raw_namespace
        if raw_namespace:
            return raw_namespace

        prefix = obj.prefix
        if prefix:
            return obj.ns_map.get(prefix)

        if obj.is_qualified:
            return target_namespace

        return "" if isinstance(obj, Element) else None

    @classmethod
    def children_extensions(
        cls, obj: ElementBase, target: Class
    ) -> Iterator[Extension]:
        """
        Recursively find and return all target's Extension classes.

        If the initial given obj has a type attribute include it in
        result.
        """
        for child in obj.children():
            if child.is_attribute:
                continue

            for ext in child.extensions:
                yield cls.build_class_extension(
                    target, ext, child.index, child.get_restrictions()
                )

            yield from cls.children_extensions(child, target)

    @classmethod
    def build_class_extension(
        cls, target: Class, name: str, index: int, restrictions: Dict
    ) -> Extension:
        """Create an extension for the target class."""
        return Extension(
            type=cls.build_data_type(target, name, index=index),
            restrictions=Restrictions(**restrictions),
        )

    @classmethod
    def build_class_attribute(
        cls, target: Class, obj: ElementBase, parent_restrictions: Restrictions
    ):
        """Generate and append an attribute field to the target class."""

        target.ns_map.update(obj.ns_map)
        types = cls.build_class_attribute_types(target, obj)
        restrictions = Restrictions.from_element(obj)

        if obj.class_name in (Tag.ELEMENT, Tag.ANY):
            restrictions.merge(parent_restrictions)

        if restrictions.prohibited:
            return

        name = obj.real_name
        target.attrs.append(
            Attr(
                index=obj.index,
                name=name,
                local_name=name,
                default=obj.default_value,
                fixed=obj.is_fixed,
                types=types,
                tag=obj.class_name,
                help=obj.display_help,
                namespace=cls.element_namespace(obj, target.qname.namespace),
                restrictions=restrictions,
            )
        )

    @classmethod
    def build_class_attribute_types(
        cls, target: Class, obj: ElementBase
    ) -> List[AttrType]:
        """Convert real type and anonymous inner types to an attribute type
        list."""
        types = [cls.build_data_type(target, name) for name in obj.real_type.split()]

        for inner in cls.build_inner_classes(
            obj, target.module, target.qname.namespace
        ):
            target.inner.append(inner)
            types.append(cls.build_data_type(target, name=inner.name, forward=True))

        if len(types) == 0:
            types.append(cls.build_data_type(target, name=obj.default_type))

        return types

    @classmethod
    def build_inner_classes(
        cls, obj: ElementBase, module: str, namespace: Optional[str]
    ) -> Iterator[Class]:
        """Find and convert anonymous types to a class instances."""
        if isinstance(obj, SimpleType) and obj.is_enumeration:
            yield cls.build_class(obj, obj.class_name, module, namespace)
        else:
            for child in obj.children():
                if isinstance(child, ComplexType) or (
                    isinstance(child, SimpleType) and child.is_enumeration
                ):
                    child.name = obj.real_name
                    yield cls.build_class(child, obj.class_name, module, namespace)
                else:
                    yield from cls.build_inner_classes(child, module, namespace)

    @classmethod
    def is_class(cls, item: ElementBase) -> bool:
        return isinstance(
            item, (SimpleType, ComplexType, Group, AttributeGroup, Element, Attribute),
        )
