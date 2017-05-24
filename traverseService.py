
# Copyright Notice:
# Copyright 2016 Distributed Management Task Force, Inc. All rights reserved.
# License: BSD 3-Clause License. For full text see link:
# https://github.com/DMTF/Redfish-Service-Validator/LICENSE.md

from bs4 import BeautifulSoup
import configparser
import requests
import io, os, sys, re
from datetime import datetime
from collections import Counter, OrderedDict
from functools import lru_cache
import logging

rsvLogger = logging.getLogger(__name__)
rsvLogger.setLevel(logging.DEBUG)
ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.INFO)
rsvLogger.addHandler(ch)
confDict = dict()
confDict['configSet'] = False
useSSL = ConfigURI = User = Passwd = sysDescription = SchemaLocation = chkCert = localOnly = serviceOnly = None

# Make logging blocks for each SingleURI Validate
def getLogger():
    return rsvLogger

# Read config info from ini file placed in config folder of tool

def setConfig(filename):
    global useSSL, ConfigURI, User, Passwd, sysDescription, SchemaLocation, chkCert, localOnly, serviceOnly
    config = configparser.ConfigParser()
    config.read(filename)
    useSSL = config.getboolean('Options', 'UseSSL')

    ConfigURI = ('https' if useSSL else 'http') + '://' + \
        config.get('SystemInformation', 'TargetIP')
    User = config.get('SystemInformation', 'UserName')
    Passwd = config.get('SystemInformation', 'Password')
    sysDescription = config.get('SystemInformation', 'SystemInfo')

    SchemaLocation = config.get('Options', 'MetadataFilePath')
    chkCert = config.getboolean('Options', 'CertificateCheck') and useSSL
    localOnly = config.getboolean('Options', 'LocalOnlyMode')
    serviceOnly = config.getboolean('Options', 'ServiceMode')

    confDict['configSet'] = True

def isConfigSet():
    if confDict['configSet']:
        return True
    else:
        raise Exception("Configuration is not set")

@lru_cache(maxsize=64)
def callResourceURI(URILink):
    """
    Makes a call to a given URI or URL

    param arg1: path to URI "/example/1", or URL "http://example.com"
    return: (success boolean, data)
    """ 
    # rs-assertions: 6.4.1, including accept, content-type and odata-versions
    # rs-assertion: handle redirects?  and target permissions
    # rs-assertion: require no auth for serviceroot calls
    nonService = 'http' in URILink[:8]
    statusCode = ''
    if not nonService:
        # feel free to make this into a regex
        noauthchk = \
            ('/redfish' in URILink and '/redfish/v1' not in URILink) or\
           URILink in ['/redfish/v1', '/redfish/v1/', '/redfish/v1/odata', 'redfish/v1/odata/'] or\
            '/redfish/v1/$metadata' in URILink
        if noauthchk:
            rsvLogger.debug('dont chkauth')
            auth = None
        else:
            auth = (User, Passwd)
    
    if nonService and serviceOnly:
        rsvLogger.info('Disallowed out of service URI')
        return False, None, -1

    # rs-assertion: do not send auth over http
    if not useSSL or nonService:
        auth = None
    
    # rs-assertion: must have application/json or application/xml
    rsvLogger.debug('callingResourceURI: %s', URILink)
    try:
        response = requests.get(ConfigURI + URILink if not nonService else URILink,
                                auth=auth, verify=chkCert)
        expCode = [200]
        statusCode = response.status_code
        rsvLogger.debug('%s, %s, %s', statusCode, expCode, response.headers)
        if statusCode in expCode:
            contenttype = response.headers.get('content-type')
            if contenttype is not None and 'application/json' in contenttype:
                decoded = response.json(object_pairs_hook=OrderedDict)
                # navigate fragment
                if '#' in URILink:
                    URILink, frag = tuple(URILink.rsplit('#',1))
                    fragNavigate = frag.split('/')[1:]
                    for item in fragNavigate:
                        if isinstance( decoded, dict ):
                            decoded = decoded.get(item)
                        elif isinstance( decoded, list ):
                            decoded = decoded[int(item)] if int(item) < len(decoded) else None
                    if not isinstance( decoded, dict ):
                        rsvLogger.warn(URILink + " decoded object no longer a dictionary")
            else:
                decoded = response.text
            return decoded is not None, decoded, statusCode
    except Exception as ex:
        rsvLogger.exception("Something went wrong")
    return False, None, statusCode


# note: Use some sort of re expression to parse SchemaAlias
# ex: #Power.1.1.1.Power , #Power.v1_0_0.Power
def getNamespace(string):
    return string.replace('#', '').rsplit('.', 1)[0]
def getType(string):
    return string.replace('#', '').rsplit('.', 1)[-1]


@lru_cache(maxsize=64)
def getSchemaDetails(SchemaAlias, SchemaURI=None, suffix='_v1.xml'):
    """
    Find Schema file for given Namespace.

    param arg1: Schema Namespace, such as ServiceRoot
    param SchemaURI: uri to grab schema, given localOnly is False
    return: (success boolean, a Soup object)
    """
    if SchemaAlias is None:
        return False, None, None
    
    # rs-assertion: parse frags
    if SchemaURI is not None and not localOnly:
        success, data, status = callResourceURI(SchemaURI)
        if success:
            soup = BeautifulSoup(data, "html.parser")
            if '#' in SchemaURI: 
                SchemaURI, frag = tuple(SchemaURI.rsplit('#',1))
                refType, refLink = getReferenceDetails(soup).get(getNamespace(frag),(None,None))
                if refLink is not None:
                    success, data, status = callResourceURI(refLink)
                    if success:
                        soup = BeautifulSoup(data, "html.parser")
                        return True, soup, refLink
                    else:
                        rsvLogger.error("SchemaURI couldn't call reference link: %s %s", SchemaURI, frag)
                else:
                    rsvLogger.error("SchemaURI missing reference link: %s %s", SchemaURI, frag)
            else:
                return True, soup, SchemaURI
        rsvLogger.error("SchemaURI unsuccessful: %s", SchemaURI)
    return False, None, None
   
def getSchemaDetailsLocal(SchemaAlias, SchemaURI=None, suffix='_v1.xml'):
    # Use local if no URI or LocalOnly
    Alias = getNamespace(SchemaAlias).split('.')[0]
    try:
        filehandle = open(SchemaLocation + '/' + Alias + suffix, "r")
        filedata = filehandle.read()
        filehandle.close()
        soup = BeautifulSoup(filedata, "html.parser")
        parentTag = soup.find('edmx:dataservices')
        child = parentTag.find('schema')
        SchemaNamespace = child['namespace']
        FoundAlias = SchemaNamespace.split(".")[0]
        if FoundAlias == Alias:
            return True, soup, "local" + SchemaLocation + '/' + Alias + suffix
    except Exception as ex:
        rsvLogger.exception("Something went wrong")
    return False, None, None

def getReferenceDetails(soup):
    """
    Create a reference dictionary from a soup file

    param arg1: soup
    return: dictionary
    """
    refDict = {}
    refs = soup.find_all('edmx:reference')
    for ref in refs:
        includes = ref.find_all('edmx:include')
        for item in includes:
            if item.get('namespace') is None or ref.get('uri') is None:
                rsvLogger.error("Reference incorrect for: ", item)
                continue
            if item.get('alias') is not None:
                refDict[item['alias']] = (item['namespace'], ref['uri'])
            else:
                refDict[item['namespace']] = (item['namespace'], ref['uri'])
                refDict[item['namespace'].split('.')[0]] = (item['namespace'], ref['uri'])
    return refDict


def getParentType(soup, refs, currentType, tagType='entitytype'):
    """
    Get parent type of given type.

    param arg1: soup
    param arg2: refs
    param arg3: current type
    param tagType: the type of tag for inheritance, default 'entitytype'
    return: success, associated soup, associated ref, new type
    """
    propSchema = soup.find( 'schema', attrs={'namespace': getNamespace(currentType)})
    
    if propSchema is None:
        return False, None, None, None
    propEntity = propSchema.find( tagType, attrs={'name': getType(currentType)})
    
    if propEntity is None:
        return False, None, None, None

    currentType = propEntity.get('basetype')
    if currentType is None:
        return False, None, None, None
    
    currentType = currentType.replace('#','')
    SchemaNamespace, SchemaType = getNamespace(currentType), getType(currentType)
    propSchema = soup.find( 'schema', attrs={'namespace': SchemaNamespace})

    if propSchema is None:
        success, innerSoup, uri = getSchemaDetails(
            *refs.get(SchemaNamespace, (None,None)))
        if not success:
            return False, None, None, None
        innerRefs = getReferenceDetails(innerSoup)
        propSchema = innerSoup.find(
            'schema', attrs={'namespace': SchemaNamespace})
        if propSchema is None:
            return False, None, None, None
    else:
        innerSoup = soup
        innerRefs = refs

    return True, innerSoup, innerRefs, currentType 

def getTypeDetails(soup, refs, SchemaAlias, tagType):
    """
    Gets list of surface level properties for a given SchemaAlias,
    
    param arg1: soup
    param arg2: references
    param arg3: SchemaAlias string
    param arg4: tag of Type, which can be EntityType or ComplexType...
    return: list of (soup, ref, string PropertyName, tagType)
    """
    PropertyList = list()

    SchemaNamespace, SchemaType = getNamespace(SchemaAlias), getType(SchemaAlias)

    rsvLogger.debug("Schema is %s, %s, %s", SchemaAlias,
                    SchemaType, SchemaNamespace)

    innerschema = soup.find('schema', attrs={'namespace': SchemaNamespace})

    if innerschema is None:
        rsvLogger.error("Got XML, but schema still doesn't exist...? %s, %s" %
                            (getNamespace(SchemaAlias), SchemaAlias))
        raise Exception('exceptionType: Was not able to get type, is Schema in XML? '  + str(refs.get(getNamespace(SchemaAlias), (getNamespace(SchemaAlias), None))))

    for element in innerschema.find_all(tagType, attrs={'name': SchemaType}):
        rsvLogger.debug("___")
        rsvLogger.debug(element['name'])
        rsvLogger.debug(element.attrs)
        rsvLogger.debug(element.get('basetype'))
        
        usableProperties = element.find_all('property')
        usableNavProperties = element.find_all('navigationproperty')
       
        for innerelement in usableProperties + usableNavProperties:
            rsvLogger.debug(innerelement['name'])
            rsvLogger.debug(innerelement.get('type'))
            rsvLogger.debug(innerelement.attrs)
            newProp = innerelement['name']
            if SchemaAlias:
                newProp = SchemaAlias + ':' + newProp
            rsvLogger.debug("ADDING :::: %s", newProp)
            if newProp not in PropertyList:
                PropertyList.append((soup,refs,newProp,tagType))
        
    return PropertyList 


def getPropertyDetails(soup, refs, PropertyItem, tagType='entitytype'):
    """
    Get dictionary of tag attributes for properties given, including basetypes.

    param arg1: soup data
    param arg2: references
    param arg3: a property string
    """
    propEntry = dict()

    propOwner, propChild = PropertyItem.split(':')[0].replace('#',''), PropertyItem.split(':')[-1]
    SchemaNamespace, SchemaType = getNamespace(propOwner), getType(propOwner)
    rsvLogger.debug('___')
    rsvLogger.debug('%s, %s', SchemaNamespace, PropertyItem)

    propSchema = soup.find('schema', attrs={'namespace': SchemaNamespace})
    if propSchema is None:
        rsvLogger.error("innerSoup doesn't exist...? %s", SchemaNamespace)
        return None
    else:
        innerSoup = soup
        innerRefs = refs

    # get type tag and tag of property in type
    propEntity = propSchema.find(tagType, attrs={'name': SchemaType})
    propTag = propEntity.find('property', attrs={'name': propChild})

    # check if this property is a nav property
    # Checks if this prop is an annotation
    propEntry['isNav'] = False
    if '@' not in propChild:
        if propTag is None:
            propTag = propEntity.find(
                'navigationproperty', attrs={'name': propChild})
            propEntry['isNav'] = True
        # start adding attrs and props together
        propAll = propTag.find_all()
        for tag in propAll:
            propEntry[tag['term']] = tag.attrs 
    else:
        propTag = propEntity

    propEntry['attrs'] = propTag.attrs
    rsvLogger.debug(propEntry)

    success, typeSoup, typeRefs, propType = getParentType(innerSoup, innerRefs, SchemaType, tagType)
    propType = propTag.get('type')
    propEntry['realtype'] = 'none'
    
    # find the real type of this, by inheritance
    while propType is not None:
        rsvLogger.debug("HASTYPE")
        TypeNamespace, TypeSpec = getNamespace(propType), getType(propType)

        rsvLogger.debug('%s, %s', TypeNamespace, propType)
        # Type='Collection(Edm.String)'
        # If collection, check its inside type
        if re.match('Collection(.*)', propType) is not None:
            propType = propType.replace('Collection(', "").replace(')', "")
            propEntry['isCollection'] = propType
            continue
        if 'Edm' in propType:
            propEntry['realtype'] = propType
            break
        
        # get proper soup
        if TypeNamespace.split('.')[0] != SchemaNamespace.split('.')[0]:
            success, typeSoup, uri = getSchemaDetails(*refs.get(TypeNamespace,(None,None)))
        else:
            success, typeSoup = True, innerSoup

        if not success:
            rsvLogger.error("innerSoup doesn't exist...? %s", SchemaNamespace)
            return propEntry

        propEntry['soup'] = typeSoup
        
        # traverse tags to find the type
        typeRefs = getReferenceDetails(typeSoup)
        # traverse tags to find the type
        typeSchema = typeSoup.find( 'schema', attrs={'namespace': TypeNamespace})
        typeSimpleTag = typeSchema.find( 'typedefinition', attrs={'name': TypeSpec})
        typeComplexTag = typeSchema.find( 'complextype', attrs={'name': TypeSpec})
        typeEnumTag = typeSchema.find('enumtype', attrs={'name': TypeSpec})
        typeEntityTag = typeSchema.find('entitytype', attrs={'name': TypeSpec})

        # perform more logic for each type
        if typeSimpleTag is not None:
            propType = typeSimpleTag.get('underlyingtype')
            isEnum = typeSimpleTag.find('annotation', attrs={'term':'Redfish.Enumeration'})
            if propType == 'Edm.String' and isEnum is not None:
                propEntry['realtype'] = 'deprecatedEnum'
                propEntry['typeprops'] = list()
                memberList = isEnum.find('collection').find_all('propertyvalue')

                for member in memberList:
                    propEntry['typeprops'].append( member.get('string'))
                rsvLogger.debug("%s", str(propEntry['typeprops']))
                break
            else:
                continue
        elif typeComplexTag is not None:
            rsvLogger.debug("go deeper in type")
            propertyList = list()
            success, baseSoup, baseRefs, baseType = True, typeSoup, typeRefs, propType
            while success:
                propertyList.extend(getTypeDetails(
                    baseSoup, baseRefs, baseType, 'complextype'))
                success, baseSoup, baseRefs, baseType = getParentType(baseSoup, baseRefs, baseType, 'complextype')
            propDict = {item[2]: getPropertyDetails( *item) for item in propertyList}
            rsvLogger.debug(key for key in propDict)
            propEntry['realtype'] = 'complex'
            propEntry['typeprops'] = propDict
            break
        elif typeEnumTag is not None:
            propEntry['realtype'] = 'enum'
            propEntry['typeprops'] = list()
            for MemberName in typeEnumTag.find_all('member'):
                propEntry['typeprops'].append(MemberName['name'])
            break
        elif typeEntityTag is not None:
            propEntry['realtype'] = 'entity'
            propEntry['typeprops'] = dict()
            rsvLogger.debug("typeEntityTag found %s", propTag['name'])
            break
        else:
            rsvLogger.error("type doesn't exist? %s", propType)
            raise Exception("getPropertyDetails: problem grabbing type: " + propType)
            break

    return propEntry


def getAllLinks(jsonData, propDict, refDict, prefix='', context=''):
    """
    Function that returns all links provided in a given JSON response.
    This result will include a link to itself.

    :param arg1: json dict
    :param arg2: property dict
    :param linkName: json dict
    :return: list of links
    """
    linkList = OrderedDict()
    # check keys in propertyDictionary
    # if it is a Nav property, check that it exists
    #   if it is not a Nav Collection, add it to list
    #   otherwise, add everything IN Nav collection
    # if it is a Complex property, check that it exists
    #   if it is, recurse on collection or individual item
    for key in propDict:
        item = getType(key).split(':')[-1]
        if propDict[key]['isNav']:
            insideItem = jsonData.get(item)
            if insideItem is not None:
                cType = propDict[key].get('isCollection') 
                autoExpand = propDict[key].get('OData.AutoExpand',None) is not None or\
                    propDict[key].get('OData.AutoExpand'.lower(),None) is not None
                if cType is not None:
                    cSchema = refDict.get(getNamespace(cType),(None,None))[1]
                    if cSchema is None:
                        cSchema = context 
                    for cnt, listItem in enumerate(insideItem):
                        linkList[prefix+str(item)+'.'+getType(propDict[key]['isCollection']) +
                                 '#' + str(cnt)] = (listItem.get('@odata.id'), autoExpand, cType, cSchema, listItem)
                else:
                    cType = propDict[key]['attrs'].get('type')
                    cSchema = refDict.get(getNamespace(cType),(None,None))[1]
                    if cSchema is None:
                        cSchema = context 
                    linkList[prefix+str(item)+'.'+getType(propDict[key]['attrs']['name'])] = (\
                            insideItem.get('@odata.id'), autoExpand, cType, cSchema, insideItem)
    for key in propDict:
        item = getType(key).split(':')[-1]
        if propDict[key]['realtype'] == 'complex':
            if jsonData.get(item) is not None:
                if propDict[key].get('isCollection') is not None:
                    for listItem in jsonData[item]:
                        linkList.update(getAllLinks(
                            listItem, propDict[key]['typeprops'], refDict, prefix+item+'.', context))
                else:
                    linkList.update(getAllLinks(
                        jsonData[item], propDict[key]['typeprops'], refDict, prefix+item+'.', context))
    rsvLogger.debug(str(linkList))
    return linkList

def getAnnotations(soup, refs, decoded, prefix=''):
    additionalProps = list() 
    for key in [k for k in decoded if prefix+'@' in k]:
        splitKey = key.split('@',1)
        fullItem = splitKey[1]
        realType, refLink = refs.get(getNamespace(fullItem),(None,None))
        success, annotationSoup, uri = getSchemaDetails(realType, refLink)
        rsvLogger.debug('%s, %s, %s, %s, %s', str(success), key, splitKey, decoded[key], realType)
        if success:
            realItem = realType + '.' + fullItem.split('.',1)[1]
            annotationRefs = getReferenceDetails(annotationSoup)
            additionalProps.append( (annotationSoup, annotationRefs, realItem+':'+key, 'term') )

    return True, additionalProps 

def checkPayloadCompliance(uri, decoded):
    messages = dict()
    success = True
    for key in [k for k in decoded if '@odata' in k]:
        itemType = key.split('.',1)[-1]
        itemTarget = key.split('.',1)[0]
        paramPass = False
        if key == 'id':
            paramPass = isinstance( decoded[key], str)
            paramPass = re.match('(\/.*)+(#([a-zA-Z0-9_.-]*\.)+[a-zA-Z0-9_.-]*)?', decoded[key]) is not None
            pass
        elif key == 'count':
            paramPass = isinstance( decoded[key], int)
            pass
        elif key == 'context':
            paramPass = isinstance( decoded[key], str)
            paramPass = re.match('(\/.*)+#([a-zA-Z0-9_.-]*\.)+[a-zA-Z0-9_.-]*', decoded[key]) is not None
            pass
        elif key == 'type':
            paramPass = isinstance( decoded[key], str)
            paramPass = re.match('#([a-zA-Z0-9_.-]*\.)+[a-zA-Z0-9_.-]*', decoded[key]) is not None
            pass
        else:
            paramPass = True
        if not paramPass:
            rsvLogger.error(key + "@odata item not compliant: " + decoded[key])
            success = False
        messages[key] = (decoded[key], 'odata',
                                         'Exists',
                                         'PASS' if paramPass else 'FAIL')
    return success, messages

def validateURITree(URI, uriName, funcValidate, expectedType=None, expectedSchema=None, expectedJson=None, allLinks=None):
    def executeLink(linkItem):
        linkURI, autoExpand, linkType, linkSchema, innerJson = linkItem

        if linkType is not None and autoExpand:
            returnVal = validateURITree(
                    linkURI, uriName + ' -> ' + linkName, funcValidate, linkType, linkSchema, innerJson, allLinks)
        else:
            returnVal = validateURITree(
                    linkURI, uriName + ' -> ' + linkName, funcValidate, allLinks=allLinks)
        rsvLogger.info('%s, %s', linkName, returnVal[1])
        return returnVal

    top = allLinks is None
    if top:
        allLinks = set()
    allLinks.add(URI)
    refLinks = OrderedDict()
    
    validateSuccess, counts, results, links = \
            funcValidate(URI, uriName, expectedType, expectedSchema, expectedJson)
    if validateSuccess:
        for linkName in links:
            if 'Links' in linkName.split('.',1)[0] or 'RelatedItem' in linkName.split('.',1)[0] or 'Redundancy' in linkName.split('.',1)[0]:
                refLinks[linkName] = links[linkName]
                continue
            if links[linkName][0] in allLinks:
                counts['repeat'] += 1
                continue

            success, linkCounts, linkResults, xlinks = executeLink(links[linkName])
            refLinks.update(xlinks)
            if not success:
                counts['unvalidated'] += 1
            results.update(linkResults)

    if top:
        for linkName in refLinks:
            if refLinks[linkName][0] not in allLinks:
                rsvLogger.info('%s, %s', linkName, refLinks[linkName])
                counts['reflink'] += 1
            else:
                continue
            
            success, linkCounts, linkResults, xlinks = executeLink(links[linkName])
            if not success:
                counts['unvalidatedRef'] += 1
            results.update(linkResults)
    
    return validateSuccess, counts, results, refLinks


