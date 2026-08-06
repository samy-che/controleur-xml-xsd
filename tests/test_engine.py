"""Tests du moteur de correction.

    python3 -m unittest discover -s tests -v

Les cas couvrent surtout les constructions XSD ou un reordonnancement naif
casserait le document : xs:all, groupes repetes, choix illimites, extensions.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xsdfix.corrector import Options
from xsdfix.service import (
    STATUS_FAILED,
    STATUS_FIXED,
    STATUS_PARTIAL,
    STATUS_VALID,
    InputFile,
    analyze,
)

HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'xmlns="urn:t" targetNamespace="urn:t" elementFormDefault="qualified">')


def run(xsd, xml, options=None, extra_xsd=None):
    files = [InputFile("main.xsd", xsd.encode("utf-8"))]
    for name, content in (extra_xsd or {}).items():
        files.append(InputFile(name, content.encode("utf-8")))
    report = analyze(files, [InputFile("doc.xml", xml.encode("utf-8"))],
                     options or Options())
    assert report.schema_error is None, report.schema_error
    return report.results[0]


def order_of(result, tag_of_interest=None):
    """Noms des balises du XML corrige, dans l'ordre, au premier niveau."""
    from lxml import etree
    root = etree.fromstring(result.corrected)
    return [etree.QName(c).localname for c in root if isinstance(c.tag, str)]


class TestOrdering(unittest.TestCase):

    def test_sequence_simple(self):
        xsd = HEAD + '''
          <xs:element name="R"><xs:complexType><xs:sequence>
            <xs:element name="A" type="xs:string"/>
            <xs:element name="B" type="xs:string"/>
            <xs:element name="C" type="xs:string"/>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        xml = '<R xmlns="urn:t"><C>3</C><A>1</A><B>2</B></R>'
        result = run(xsd, xml)
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertEqual(order_of(result), ["A", "B", "C"])

    def test_deja_conforme_non_modifie(self):
        xsd = HEAD + '''
          <xs:element name="R"><xs:complexType><xs:sequence>
            <xs:element name="A" type="xs:string"/>
            <xs:element name="B" type="xs:string"/>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        result = run(xsd, '<R xmlns="urn:t"><A>1</A><B>2</B></R>')
        self.assertEqual(result.status, STATUS_VALID)
        self.assertEqual(result.changes, [])
        self.assertIsNone(result.corrected)

    def test_xs_all_ordre_libre_non_touche(self):
        """xs:all accepte n'importe quel ordre : rien ne doit bouger."""
        xsd = HEAD + '''
          <xs:element name="R"><xs:complexType><xs:all>
            <xs:element name="A" type="xs:string"/>
            <xs:element name="B" type="xs:string"/>
          </xs:all></xs:complexType></xs:element>
        </xs:schema>'''
        result = run(xsd, '<R xmlns="urn:t"><B>2</B><A>1</A></R>')
        self.assertEqual(result.status, STATUS_VALID)

    def test_groupe_repete_non_melange(self):
        """(A,B)* : le correcteur ne doit pas regrouper les A puis les B."""
        xsd = HEAD + '''
          <xs:element name="R"><xs:complexType>
            <xs:sequence maxOccurs="unbounded">
              <xs:element name="A" type="xs:string"/>
              <xs:element name="B" type="xs:string"/>
            </xs:sequence>
          </xs:complexType></xs:element>
        </xs:schema>'''
        xml = '<R xmlns="urn:t"><A>1</A><B>1</B><A>2</A><B>2</B></R>'
        result = run(xsd, xml)
        self.assertEqual(result.status, STATUS_VALID)

    def test_groupe_repete_precede_dun_element(self):
        """Z doit remonter devant le groupe repete, sans casser l'alternance."""
        xsd = HEAD + '''
          <xs:element name="R"><xs:complexType><xs:sequence>
            <xs:element name="Z" type="xs:string"/>
            <xs:sequence maxOccurs="unbounded">
              <xs:element name="A" type="xs:string"/>
              <xs:element name="B" type="xs:string"/>
            </xs:sequence>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        xml = '<R xmlns="urn:t"><A>1</A><B>1</B><A>2</A><B>2</B><Z>z</Z></R>'
        result = run(xsd, xml)
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertEqual(order_of(result), ["Z", "A", "B", "A", "B"])

    def test_choice_illimite_ordre_preserve(self):
        """<xs:choice maxOccurs="unbounded"> autorise tout ordre : ne rien bouger."""
        xsd = HEAD + '''
          <xs:element name="R"><xs:complexType>
            <xs:choice maxOccurs="unbounded">
              <xs:element name="A" type="xs:string"/>
              <xs:element name="B" type="xs:string"/>
            </xs:choice>
          </xs:complexType></xs:element>
        </xs:schema>'''
        result = run(xsd, '<R xmlns="urn:t"><B>1</B><A>2</A><B>3</B></R>')
        self.assertEqual(result.status, STATUS_VALID)

    def test_extension_base_puis_extension(self):
        """Un type derive place d'abord le contenu de base, puis le sien."""
        xsd = HEAD + '''
          <xs:complexType name="Base"><xs:sequence>
            <xs:element name="A" type="xs:string"/>
            <xs:element name="B" type="xs:string"/>
          </xs:sequence></xs:complexType>
          <xs:complexType name="Derive">
            <xs:complexContent><xs:extension base="Base"><xs:sequence>
              <xs:element name="C" type="xs:string"/>
            </xs:sequence></xs:extension></xs:complexContent>
          </xs:complexType>
          <xs:element name="R" type="Derive"/>
        </xs:schema>'''
        result = run(xsd, '<R xmlns="urn:t"><C>3</C><B>2</B><A>1</A></R>')
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertEqual(order_of(result), ["A", "B", "C"])

    def test_groupe_nomme(self):
        xsd = HEAD + '''
          <xs:group name="G"><xs:sequence>
            <xs:element name="A" type="xs:string"/>
            <xs:element name="B" type="xs:string"/>
          </xs:sequence></xs:group>
          <xs:element name="R"><xs:complexType><xs:sequence>
            <xs:group ref="G"/>
            <xs:element name="C" type="xs:string"/>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        result = run(xsd, '<R xmlns="urn:t"><C>3</C><B>2</B><A>1</A></R>')
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertEqual(order_of(result), ["A", "B", "C"])

    def test_groupe_de_substitution(self):
        xsd = HEAD + '''
          <xs:element name="Paiement" type="xs:string" abstract="true"/>
          <xs:element name="Virement" type="xs:string" substitutionGroup="Paiement"/>
          <xs:element name="R"><xs:complexType><xs:sequence>
            <xs:element name="A" type="xs:string"/>
            <xs:element ref="Paiement"/>
            <xs:element name="Z" type="xs:string"/>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        xml = '<R xmlns="urn:t"><Z>z</Z><Virement>v</Virement><A>a</A></R>'
        result = run(xsd, xml)
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertEqual(order_of(result), ["A", "Virement", "Z"])

    def test_elements_locaux_non_qualifies(self):
        xsd = ('<?xml version="1.0"?>'
               '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
               'xmlns="urn:t" targetNamespace="urn:t" elementFormDefault="unqualified">'
               '<xs:element name="R"><xs:complexType><xs:sequence>'
               '<xs:element name="A" type="xs:string"/>'
               '<xs:element name="B" type="xs:string"/>'
               '</xs:sequence></xs:complexType></xs:element></xs:schema>')
        # en "unqualified", seule la racine porte l'espace de noms
        result = run(xsd, '<t:R xmlns:t="urn:t"><B>2</B><A>1</A></t:R>')
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertEqual(order_of(result), ["A", "B"])

    def test_racine_qualifiee_enfants_a_degrader(self):
        """XSD unqualified mais XML entierement qualifie : on retire le ns des enfants."""
        xsd = ('<?xml version="1.0"?>'
               '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
               'xmlns="urn:t" targetNamespace="urn:t" elementFormDefault="unqualified">'
               '<xs:element name="R"><xs:complexType><xs:sequence>'
               '<xs:element name="A" type="xs:string"/>'
               '</xs:sequence></xs:complexType></xs:element></xs:schema>')
        result = run(xsd, '<R><A>1</A></R>')
        self.assertEqual(result.status, STATUS_FIXED)
        from lxml import etree
        root = etree.fromstring(result.corrected)
        self.assertEqual(root.tag, "{urn:t}R")
        self.assertEqual(root[0].tag, "A")   # l'enfant reste sans espace de noms

    def test_imbrication_profonde(self):
        xsd = HEAD + '''
          <xs:element name="R"><xs:complexType><xs:sequence>
            <xs:element name="N" maxOccurs="unbounded"><xs:complexType><xs:sequence>
              <xs:element name="A" type="xs:string"/>
              <xs:element name="B" type="xs:string"/>
            </xs:sequence></xs:complexType></xs:element>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        xml = ('<R xmlns="urn:t"><N><B>1</B><A>1</A></N>'
               '<N><B>2</B><A>2</A></N></R>')
        result = run(xsd, xml)
        self.assertEqual(result.status, STATUS_FIXED)
        from lxml import etree
        root = etree.fromstring(result.corrected)
        for node in root:
            self.assertEqual([etree.QName(c).localname for c in node], ["A", "B"])


class TestNamespace(unittest.TestCase):

    def test_ajout_du_namespace(self):
        xsd = HEAD + '''
          <xs:element name="R"><xs:complexType><xs:sequence>
            <xs:element name="A" type="xs:string"/>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        result = run(xsd, '<R><A>1</A></R>')
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertIn(b'xmlns="urn:t"', result.corrected)

    def test_suppression_du_namespace(self):
        xsd = ('<?xml version="1.0"?>'
               '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
               '<xs:element name="R"><xs:complexType><xs:sequence>'
               '<xs:element name="A" type="xs:string"/>'
               '</xs:sequence></xs:complexType></xs:element></xs:schema>')
        result = run(xsd, '<R xmlns="urn:faux"><A>1</A></R>')
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertNotIn(b"urn:faux", result.corrected)

    def test_namespace_desactive(self):
        xsd = HEAD + '''
          <xs:element name="R"><xs:complexType><xs:sequence>
            <xs:element name="A" type="xs:string"/>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        result = run(xsd, '<R><A>1</A></R>', Options(fix_namespace=False))
        self.assertEqual(result.status, STATUS_FAILED)


class TestOptions(unittest.TestCase):

    XSD = HEAD + '''
      <xs:element name="R"><xs:complexType><xs:sequence>
        <xs:element name="A" type="xs:string"/>
        <xs:element name="B" type="xs:string" minOccurs="0"/>
      </xs:sequence></xs:complexType></xs:element>
    </xs:schema>'''

    def test_ajout_element_obligatoire(self):
        xml = '<R xmlns="urn:t"><B>2</B></R>'
        base = run(self.XSD, xml)
        self.assertEqual(base.status, STATUS_FAILED)
        with_insert = run(self.XSD, xml, Options(insert_missing=True))
        self.assertEqual(with_insert.status, STATUS_FIXED)
        self.assertEqual(order_of(with_insert), ["A", "B"])

    def test_suppression_balise_inconnue(self):
        xml = '<R xmlns="urn:t"><A>1</A><Inconnu>x</Inconnu></R>'
        base = run(self.XSD, xml)
        self.assertEqual(base.status, STATUS_FAILED)
        with_remove = run(self.XSD, xml, Options(remove_unknown=True))
        self.assertEqual(with_remove.status, STATUS_FIXED)
        self.assertNotIn(b"Inconnu", with_remove.corrected)

    def test_nettoyage_des_valeurs(self):
        xsd = HEAD + '''
          <xs:simpleType name="Code"><xs:restriction base="xs:string">
            <xs:pattern value="[A-Z]{2}"/>
          </xs:restriction></xs:simpleType>
          <xs:element name="R"><xs:complexType><xs:sequence>
            <xs:element name="A" type="Code"/>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        result = run(xsd, '<R xmlns="urn:t"><A>  FR  </A></R>')
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertIn(b"<A>FR</A>", result.corrected)


class TestPreservation(unittest.TestCase):
    """Le correcteur ne doit rien perdre de ce qu'il ne corrige pas."""

    XSD = HEAD + '''
      <xs:element name="R"><xs:complexType><xs:sequence>
        <xs:element name="A"><xs:complexType>
          <xs:simpleContent><xs:extension base="xs:string">
            <xs:attribute name="lang" type="xs:string"/>
          </xs:extension></xs:simpleContent>
        </xs:complexType></xs:element>
        <xs:element name="B" type="xs:string"/>
      </xs:sequence>
      <xs:attribute name="id" type="xs:string"/>
      </xs:complexType></xs:element>
    </xs:schema>'''

    def test_attributs_conserves(self):
        xml = '<R xmlns="urn:t" id="42"><B>b</B><A lang="fr">a</A></R>'
        result = run(self.XSD, xml)
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertIn(b'id="42"', result.corrected)
        self.assertIn(b'lang="fr"', result.corrected)

    def test_commentaires_conserves(self):
        xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!-- en-tete -->\n'
               '<R xmlns="urn:t">\n  <B>b</B>\n  <!-- note -->\n  <A>a</A>\n</R>\n')
        result = run(self.XSD, xml)
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertIn(b"<!-- en-tete -->", result.corrected)
        self.assertIn(b"<!-- note -->", result.corrected)

    def test_encodage_conserve(self):
        xml = ('<?xml version="1.0" encoding="ISO-8859-1"?>'
               '<R xmlns="urn:t"><B>café</B><A>hôtel</A></R>').encode("iso-8859-1")
        report = analyze([InputFile("main.xsd", self.XSD.encode("utf-8"))],
                         [InputFile("doc.xml", xml)], Options())
        result = report.results[0]
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertIn(b"ISO-8859-1", result.corrected)
        self.assertIn("café".encode("iso-8859-1"), result.corrected)

    def test_xml_mal_forme(self):
        report = analyze([InputFile("main.xsd", self.XSD.encode("utf-8"))],
                         [InputFile("doc.xml", b"<R><A>a</R>")], Options())
        self.assertEqual(report.results[0].status, "error")
        self.assertIn("mal formé", report.results[0].fatal)


class TestSchemaMultiFichiers(unittest.TestCase):

    def test_import_multi_namespaces_prefixes_conserves(self):
        """Cas type Factur-X / UBL : racine dans un namespace, enfants dans un autre."""
        main = ('<?xml version="1.0"?>'
                '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
                'xmlns:rsm="urn:rsm" xmlns:ram="urn:ram" targetNamespace="urn:rsm" '
                'elementFormDefault="qualified">'
                '<xs:import namespace="urn:ram" schemaLocation="ram.xsd"/>'
                '<xs:element name="Facture"><xs:complexType><xs:sequence>'
                '<xs:element ref="ram:Numero"/>'
                '<xs:element ref="ram:Date"/>'
                '<xs:element ref="ram:Montant"/>'
                '</xs:sequence></xs:complexType></xs:element></xs:schema>')
        ram = ('<?xml version="1.0"?>'
               '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
               'targetNamespace="urn:ram" elementFormDefault="qualified">'
               '<xs:element name="Numero" type="xs:string"/>'
               '<xs:element name="Date" type="xs:date"/>'
               '<xs:element name="Montant" type="xs:decimal"/>'
               '</xs:schema>')
        xml = ('<rsm:Facture xmlns:rsm="urn:rsm" xmlns:ram="urn:ram">'
               '<ram:Montant>10.00</ram:Montant>'
               '<ram:Numero>F1</ram:Numero>'
               '<ram:Date>2026-01-01</ram:Date>'
               '</rsm:Facture>')
        result = run(main, xml, extra_xsd={"ram.xsd": ram})
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertEqual(order_of(result), ["Numero", "Date", "Montant"])
        # les prefixes d'origine doivent survivre a la correction
        self.assertIn(b"<ram:Numero>", result.corrected)
        self.assertIn(b"<rsm:Facture", result.corrected)

    def test_include(self):
        main = (HEAD + '<xs:include schemaLocation="types.xsd"/>'
                '<xs:element name="R" type="RType"/></xs:schema>')
        types = (HEAD + '<xs:complexType name="RType"><xs:sequence>'
                 '<xs:element name="A" type="xs:string"/>'
                 '<xs:element name="B" type="xs:string"/>'
                 '</xs:sequence></xs:complexType></xs:schema>')
        result = run(main, '<R xmlns="urn:t"><B>2</B><A>1</A></R>',
                     extra_xsd={"types.xsd": types})
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertEqual(order_of(result), ["A", "B"])


if __name__ == "__main__":
    unittest.main()
