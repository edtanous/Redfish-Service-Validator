# Copyright Notice:
# Copyright 2016 Distributed Management Task Force, Inc. All rights reserved.
# License: BSD 3-Clause License. For full text see link: https://github.com/DMTF/Redfish-Service-Validator/LICENSE.md

from traceback import format_exc
from bs4 import BeautifulSoup
from time import strftime, strptime
from datetime import datetime as DT
import ConfigParser, glob, requests
import random, string, re
from html import HTML
import time
import os
import sys
from collections import Counter

# Read config info from ini file placed in config folder of tool
config = ConfigParser.ConfigParser()
config.read(os.path.join('.', 'config', 'config.ini'))
useSSL = config.getboolean('Options', 'UseSSL')
ConfigURI = ( 'https' if useSSL else 'http' ) + '://'+config.get('SystemInformation', 'TargetIP')
User = config.get('SystemInformation', 'UserName')
Passwd = config.get('SystemInformation', 'Password')
SchemaLocation = config.get('Options', 'MetadataFilePath')
chkCert = config.getboolean('Options', 'CertificateCheck') and useSSL
getOnly = config.getboolean('Options', 'GetOnlyMode')
debug = 0

if debug:
    print "Config details:" + str((useSSL,ConfigURI,User,Passwd,SchemaLocation,chkCert,getOnly))

ComplexTypeLinksDictionary = {'SubLinks':[]}
ComplexLinksIndex = 0
GlobalCount = 1
AllLinks = []
global SerialNumber
SerialNumber = 1
# Initiate counters for Pass/Fail report at Schema level and overall compliance level
countTotProp = countPassProp = countFailProp = countSkipProp = countWarnProp = 0
countTotSchemaProp = countPassSchemaProp = countFailSchemaProp = countSkipSchemaProp = countWarnSchemaProp = 0
countTotMandatoryProp = countPassMandatoryProp = countFailMandatoryProp = countWarnMandatoryProp = 0

# Function to GET ServiceRoot response from test system
# This call should not require authentication
def getRootURI():
    """
    Get JSON response from the Root URI of a configured server.

    :return: success, JSON dictionary, if failed then returns False, None 
    """
    return callResourceURI("ServiceRoot", '/redfish/v1')

# Function to GET/PATCH/POST resource URI
# Certificate check is conditional based on input from config ini file
# 
def callResourceURI(SchemaName, URILink, Method = 'GET', payload = None, mute = False):
        """
        Makes a call to a given URI
        
        param URILink: path to URI "/example/1"
        param Method: http message type, default 'GET'
        param payload: data for PATCH
        """
        URILink = URILink.replace("#", "%23")
        statusCode = ""
        try:
                expCode = []
                if Method == 'GET' or Method == 'ReGET':
                        response = requests.get(ConfigURI+URILink, auth = (User, Passwd), verify=chkCert)
                        expCode = [200, 204]
                elif Method == 'PATCH':
                        response = requests.patch(ConfigURI+URILink, data = payload, auth = (User, Passwd),verify=chkCert)
                        expCode = [200, 204, 400, 405]

                statusCode = response.status_code
                if debug:
                    print Method, statusCode, expCode
                if statusCode in expCode:
                   decoded = response.json()
                   return True, decoded
        except Exception as ex:
                print "Something went wrong: ", ex
                return False, None
        return False, None

# Function to parse individual Schema xml file and search for the Alias string
# Returns the content of the xml file on successfully matching the Alias
def getSchemaDetails(SchemaAlias):
        """
        Find Schema file for given Alias.
        
        param arg1: Schema Alias, such as ServiceRoot
        return: a Soup object
        """
        if '.' in SchemaAlias:
                Alias = SchemaAlias[:SchemaAlias.find('.')]
        else:
                Alias = SchemaAlias
        for filename in glob.glob(SchemaLocation):
                if Alias not in filename:
                    continue
                try:
                        filehandle = open(filename, "r")
                        filedata = filehandle.read()
                        filehandle.close()
                        soup = BeautifulSoup(filedata, "html.parser")
                        parentTag = soup.find_all('edmx:dataservices', limit=1)
                        for eachTag in parentTag:
                                for child in eachTag.find_all('schema', limit=1):
                                        SchemaNamespace = child['namespace']
                                        FoundAlias = SchemaNamespace.split(".")[0]
                                        if FoundAlias == Alias:
                                                return True, soup
                except Exception as ex:
                        print "Something went wrong: ", ex
                        return False, None 
        return False, None 


def getNamespace(string):
    return string.split('.')[0].replace('#','')
def getNamespaceVersion(string):
    spl = string.replace('#','').split('.')[:2]
    return spl[0] + "." + spl[1]
def getType(string):
    return string.split('.')[-1].replace('#','')

# Function to search for all Property attributes in any target schema
# Schema XML may be the initial file for local properties or referenced schema for foreign properties
baseTypeList = list()
def getEntityTypeDetails(soup, SchemaAlias):
        PropertyList = list()
        PropLink = ""
        SchemaType = getType(SchemaAlias)
        SchemaNamespace = getNamespace(SchemaAlias)
            
        sns = getNamespaceVersion(SchemaAlias)
        if '_' not in getNamespaceVersion(SchemaAlias):
            sns = SchemaNamespace

        print "Schema is", SchemaAlias, SchemaType
        innersoup = soup.find_all('schema',attrs={'namespace':sns})
        
        if len(innersoup) == 0:
            return PropertyList
        innersoup = innersoup[0]
        
        for element in innersoup.find_all('entitytype',attrs={'name': SchemaType}):
            print "___"
            print element['name']
            print element.attrs
            print element.get('basetype',None)

            usableProperties = element.find_all('property')
            baseType = element.get('basetype',None)
            
            print "INNER"
            if baseType is not None and baseType not in baseTypeList:
                print "**GOING IN** ", baseType
                baseTypeList.append(baseType)
                if getNamespace(baseType) != SchemaNamespace:
                    success, InnerSchemaSoup = getSchemaDetails(baseType)
                    PropertyList.extend(getEntityTypeDetails(InnerSchemaSoup, baseType))
                    if not success:
                        print 'problem'
                        break
                else: 
                    PropertyList.extend(getEntityTypeDetails(soup, baseType))

            for innerelement in usableProperties:
                print innerelement['name']
                print innerelement['type']
                print innerelement.attrs
                newProp = innerelement['name']
                if SchemaAlias:
                    newProp = SchemaAlias + '.' + newProp
                print "ADDING ::::", newProp 
                if newProp not in PropertyList: 
                    PropertyList.append( newProp )

        return PropertyList

# Function to retrieve the detailed Property attributes and store in a dictionary format
# The attributes for each property are referenced through various other methods for compliance check
def getPropertyDetails(soup, PropertyList, SchemaAlias = None):
        PropertyDictionary = dict() 
        
        for prop in PropertyList:
            print prop

        return PropertyDictionary
        def getResourcePropertyDetails(soup, PropertyName, SchemaName):
                try:
                        try:

                                if PropertyName.count(".") == 2:
                                        PropertyDetails = soup.find('property', attrs={'name':PropertyName.split(".")[-1]})
                                elif PropertyName.count(".") == 1:
                                        try:
                                                complexDetails = ""
                                                complexDetails = soup.find('complextype', attrs={'name':PropertyName.split(".")[0]})
                                                if not (complexDetails == None):
                                                        PropertyDetails = complexDetails.find('property', attrs={'name':PropertyName.split(".")[-1]})
                                                        if (PropertyDetails == None):
                                                                PropertyDetails = soup.find('property', attrs={'name':PropertyName.split(".")[-1]})
                                                else:
                                                        PropertyDetails = soup.find('property', attrs={'name':PropertyName.split(".")[-1]})
                                                        
                                                if PropertyDetails == None or PropertyDetails == "":
                                                        status, moreSoup = getSchemaDetails("Resource")
                                                        PropertyDetails = moreSoup.find('property', attrs={'name':PropertyName.split(".")[-1]})
                                        except Exception as e:
                                             if debug > 1: print "Exception has occurred: ", e  
                                else:
                                        PropertyDetails = soup.find('property', attrs={'name':PropertyName})
                        except Exception as e:
                             if debug > 1: print "Exception has occurred: ", e  
                        try:
                                status, moreSoup = getSchemaDetails("Resource")
                                key = "Resource." + PropertyName

                                if not (PropertyDetails == None):
                                        
                                        if PropertyDetails.attrs['type'] == (key):
                                                try:
        
                                                        FindAll = moreSoup.find('typedefinition', attrs={'name':PropertyDetails.attrs['type'].split(".")[-1]})
                                                        try:
                                                                FindAll.attrs['type'] = FindAll.attrs['underlyingtype']
                                                        except Exception as e:
                                                             if debug > 1: print "Exception has occurred: ", e 
                                                        PropertyDictionary [SchemaName + "-" + PropertyName+'.Attributes'] = FindAll.attrs
                                                        for propertyTerm in FindAll.find_all('annotation'):
                                                                PropertyDictionary [SchemaName + "-" + PropertyName+'.'+propertyTerm['term']] = propertyTerm.attrs
                                        
                                                except:
                                                        PropertyDictionary [SchemaName + "-" + PropertyName+'.Attributes'] = PropertyDetails.attrs
                                                        for propertyTerm in PropertyDetails.find_all('annotation'):
                                                                PropertyDictionary [SchemaName + "-" + PropertyName+'.'+propertyTerm['term']] = propertyTerm.attrs              
                                        else:
                                                
                                                try:
                                                        
                                                        FindAll = soup.find('typedefinition', attrs={'name':PropertyDetails.attrs['type'].split(".")[-1]})
                                                        try:
                                                                FindAll.attrs['type'] = FindAll.attrs['underlyingtype']
                                                        except Exception as e:
                                                                     if debug > 1: print "Exception has occurred: ", e  
                                                        PropertyDictionary [SchemaName + "-" + PropertyName+'.Attributes'] = FindAll.attrs
                                                        for propertyTerm in FindAll.find_all('annotation'):
                                                                PropertyDictionary [SchemaName + "-" + PropertyName+'.'+propertyTerm['term']] = propertyTerm.attrs
                                        
                                                except:
                                                        PropertyDictionary [SchemaName + "-" + PropertyName+'.Attributes'] = PropertyDetails.attrs
                                                        for propertyTerm in PropertyDetails.find_all('annotation'):
                                                                PropertyDictionary [SchemaName + "-" + PropertyName+'.'+propertyTerm['term']] = propertyTerm.attrs
                                
                                else:
                                        if debug:
                                            print "No details present"
                                        try:
                                                
                                                FindAll = soup.find('typedefinition', attrs={'name':PropertyName.split(".")[-1]})
                                                try:
                                                        FindAll.attrs['type'] = FindAll.attrs['underlyingtype']
                                                except Exception as e:
                                                     if debug > 1: print "Exception has occurred: ", e  
                                                PropertyDictionary [SchemaName + "-" + PropertyName+'.Attributes'] = FindAll.attrs
                                                for propertyTerm in FindAll.find_all('annotation'):
                                                        PropertyDictionary [SchemaName + "-" + PropertyName+'.'+propertyTerm['term']] = propertyTerm.attrs
                                
                                        except:
                                                PropertyDictionary [SchemaName + "-" + PropertyName+'.Attributes'] = PropertyDetails.attrs
                                                for propertyTerm in PropertyDetails.find_all('annotation'):
                                                        PropertyDictionary [SchemaName + "-" + PropertyName+'.'+propertyTerm['term']] = propertyTerm.attrs
                                
                        except Exception as e:
                             if debug > 1: print "Exception has occurred: ", e  
                except Exception as e:
                     if debug > 1: print "Exception has occurred: ", e  

        SchemaList = []
        for PropertyName in PropertyList:
                if ':' in PropertyName:
                        Alias = PropertyName[:PropertyName.find(':')]
#                       if not(Alias in SchemaList):
#                               SchemaList.append(Alias)
                        status, moreSoup = getSchemaDetails(Alias)
                        SchemaName = SchemaAlias.split(".")[-1]
                        getResourcePropertyDetails(moreSoup, PropertyName[PropertyName.find(':')+1:], SchemaName)
                elif SchemaAlias != None:
                        SchemaName = SchemaAlias.split(".")[-1]
                        getResourcePropertyDetails(soup, PropertyName, SchemaName)
                else:
                        getResourcePropertyDetails(soup, PropertyName)
        return PropertyDictionary

# Function to retrieve all possible values for any particular Property
# if Schema puts a restriction on the values that the property should have
def getEnumTypeDetails(soup, enumName):

        for child in soup.find_all('enumtype'):
                if child['name'] == enumName:
                        PropertyList1 = []
                        for MemberName in child.find_all('member'):
                                if MemberName['name'] in PropertyList1:
                                        continue
                                PropertyList1.append(MemberName['name'])
                        return PropertyList1

# Function to check compliance of individual Properties based on the attributes retrieved from the schema xml
def checkPropertyCompliance(PropertyList, PropertyDictionary, decoded, soup, SchemaName):
                resultList = dict()
                counters = Counter()
                for PropertyName in PropertyList:
                                print PropertyName
                                if ':' in PropertyName:
                                        PropertyName = PropertyName[PropertyName.find(':')+1:]

                                if 'Oem' in PropertyName:
                                        resultList[PropertyName] = (PropertyName,"Skip","No Value","No Value", "Skip check for OEM", True)
                                        print 'OEM Properties outside of Compliance Tool scope. Skipping check for the property.'
                                        print 80*'*'
                                        counters['skip'] += 1
                                        continue

                                propMandatory = False

                                try:
                                        if PropertyName.count(".") == 2:
                                                MainAttribute = midAttribute = SubAttribute = propValue = ""

                                                MainAttribute = PropertyName.split(".")[0]
                                                midAttribute = PropertyName.split(".")[1]
                                                SubAttribute = PropertyName.split(".")[-1]
                                                propValue = decoded[MainAttribute][midAttribute][SubAttribute]
                                                
                                        elif PropertyName.count(".") == 1:
                                                MainAttribute = PropertyName.split(".")[0]
                                                SubAttribute = PropertyName.split(".")[1]
                                                propValue = decoded[MainAttribute][SubAttribute]
                                        else:
                                                propValue = decoded[PropertyName]
                                except:
                                        print 'Value not found for property', PropertyName
                                        propValue = None

                                if PropertyDictionary.has_key(SchemaName + "-" + PropertyName+'.Redfish.Required') or PropertyDictionary.has_key(SchemaName + "-" + PropertyName+'.DMTF.Required'):
                                        print PropertyDictionary[SchemaName + "-" + PropertyName+'.Redfish.Required'], SchemaName + "-" + PropertyName+'.Redfish.Required'
                                        propMandatory = True
                                        counters['mandatory'] += 1
                                propAttr = PropertyDictionary.get(SchemaName + "-" + PropertyName+'.Attributes')
                                if debug: 
                                    print "propAttr:::::::::::::::::::::::::::", propAttr
                                
                                optionalFlag = True
                                if propAttr:

                                    if (propAttr.has_key('type')):
                                            propType = propAttr['type']
                                    if propAttr.has_key('nullable'):
                                            optionalFlag = False
                                            propNullable = propAttr['nullable']
                                            if (propNullable == 'false' and propValue == ''):
                                                    if PropertyDictionary.has_key(SchemaName + "-" + PropertyName+'.Redfish.RequiredOnCreate'):
                                                            resultList[PropertyName] = (PropertyName, propType + ' (Not Nullable)', propValue, propMandatory, True)
                                                    else:
                                                            resultList[PropertyName] = (PropertyName, propType + ' (Not Nullable)', propValue, propMandatory, False)
                                                    continue
                                    if (propMandatory == True and (propValue == None or propValue == '')):
                                            resultList[PropertyName] = (PropertyName, propType + ' (Not Nullable)', propValue, propMandatory, False)
                                            continue
                                    if propAttr.has_key(PropertyName+'.OData.Permissions'):
                                            propPermissions = propAttr[PropertyName+'.OData.Permissions']['enummember']
                                            if propPermissions == 'OData.Permission/ReadWrite':
                                                    print 'Check Update Functionality for', PropertyName
                                    if propValue != None:
                                            checkPropertyType(PropertyName, PropertyDictionary, propValue, propType, optionalFlag, propMandatory, soup, SchemaName)
                                    elif propValue == None:
                                            resultList[PropertyName] = (PropertyName, propType, propValue, propMandatory, None)
                                    else:
                                            resultList[PropertyName] = (PropertyName, "No Value Specified", propValue, propMandatory, None)

                print resultList
                print counters
                return resultList, counters

# Function to collect all links in current resource schema
def     getAllLinks(jsonData, linkName=None):
        """
        Function that returns all links provided in a given JSON response.
        This result will include a link to itself.

        :param jsonData: json dict
        :return: list of links, including itself
        """
        linkList = dict()
        if '@odata.id' in jsonData and linkName is not None:
            if debug:                
                print "getLink:",jsonData['@odata.id']
            linkList[linkName] = jsonData['@odata.id']
        for element in jsonData:
                value = jsonData[element]
                if type(value) is dict:
                    linkList.update( getAllLinks(value, element))
                if type(value) is list:
                    count = 0
                    for item in value:
                        if type(item) is dict:
                            linkList.update( getAllLinks(item, str(element) + "#" + str(count)))
        return linkList 

# # Function to handle sub-Links retrieved from parent URI's which are not directly accessible from ServiceRoot
def getChildLinks(PropertyList, decoded, soup):
        global ComplexTypeLinksDictionary
        global ComplexLinksFlag
        global GlobalCount
        linkList = getAllLinks(jsonData= decoded)
         
        for PropertyName, value in linkList.iteritems():
                if debug: 
                    print "PropertyName::::::::::::::::::::::::::::::::::::::::::::::", PropertyName
                    print "value:::::::::::::::::::::::::::::::::::::::::::::::::::::", value
                        
                SchemaAlias = soup.find('schema')['namespace'].split(".")[0]
                try:
                        AllComplexTypeDetails = soup.find_all('entitytype')
                except Exception as e:
                     if debug > 1: print "Exception has occurred: ", e  
                #ComplexTypeDetails = soup.find_all('entitytype')[0]
                i = 0
                for ComplexTypeDetails in AllComplexTypeDetails: 
                        for child in ComplexTypeDetails.find_all('navigationproperty'):
                                if PropertyName == child['name']:
                                        NavigationPropertyName = child['name']
                                        NavigationPropertyType = child['type']
                                        PropIndex = NavigationPropertyName+":"+NavigationPropertyType
                                        
                                        
                                        if 'Collection(' in NavigationPropertyType:
                                                for elem, data in value.iteritems():
                                                        LinkIndex = PropIndex+"_"+str(elem) + "_" + str(GlobalCount)
                                                        try:
                                                                NavigationPropertyLink = value[elem]
                                                        except:
                                                                NavigationPropertyLink = value['@odata.id']

                                                        tempFlag = False
                                                        temp = ""
                                                        for eachCount in range(0, GlobalCount):
                                                                
                                                                temp = PropIndex + "_" +str(elem) + "_" + str(eachCount)
                                                                if (temp in ComplexTypeLinksDictionary['SubLinks']) and (NavigationPropertyLink in ComplexTypeLinksDictionary[temp+'.Link']):
                                                                        tempFlag = True # Skip duplicate sublink addition
                                                                        break
                                                        if tempFlag:
                                                                continue
                                                        else:
                                                                GlobalCount = GlobalCount + 1
                                                                
                                                        
                                                        ComplexTypeLinksDictionary['SubLinks'].append(LinkIndex)
                                                        SchemaAlias = NavigationPropertyType[NavigationPropertyType.find('(')+1:NavigationPropertyType.find(')')]
                                                        ComplexTypeLinksDictionary[LinkIndex+'.Schema'] = SchemaAlias
                                                        ComplexTypeLinksDictionary[LinkIndex+'.Link'] = NavigationPropertyLink
                                                        i+=1
                                        else:
                                                PropIndexAppend = PropIndex + "_" + str(GlobalCount)
                                                NavigationPropertyLink = value
                                                try:
                                                        tempFlag = False
                                                        temp = ""
                                                        for eachCount in range(0, GlobalCount):
                                                                temp = PropIndex + "_" + str(eachCount)
                                                                
                                                                if (temp in ComplexTypeLinksDictionary['SubLinks']) and (NavigationPropertyLink in ComplexTypeLinksDictionary[temp+'.Link']):
                                                                        tempFlag = True # Skip duplicate sublink addition
                                                                        break
                                                        if tempFlag:
                                                                continue
                                                        else:
                                                                GlobalCount = GlobalCount + 1
                                                except Exception as e:
                                                         if debug > 1: print "Exception has occurred: ", e  
                                                            
                                                ComplexTypeLinksDictionary['SubLinks'].append(PropIndexAppend)                                  
                                                ComplexTypeLinksDictionary[PropIndexAppend+'.Schema'] = NavigationPropertyType
                                                ComplexTypeLinksDictionary[PropIndexAppend+'.Link'] = NavigationPropertyLink
                                                if debug:
                                                    print "ComplexTypeLinksDictionary[PropIndex+'.Schema']:::::::::::::::::::::::", ComplexTypeLinksDictionary[PropIndexAppend+'.Schema']
                                                    print "ComplexTypeLinksDictionary[PropIndex+'.Link']:::::::::::::::::::::::::", ComplexTypeLinksDictionary[PropIndexAppend+'.Link']
                                                
                                ComplexLinksFlag = True                         
                i = 0
                ComplexTypeDetails = soup.find('complextype', attrs={'name':"Links"})
                try:                    
                        for child in ComplexTypeDetails.find_all('navigationproperty'):
                                if PropertyName == child['name']:
                                        NavigationPropertyName = child['name']
                                        NavigationPropertyType = child['type']
                                        PropIndex = NavigationPropertyName+":"+NavigationPropertyType
                                        
                                        if 'Collection(' in NavigationPropertyType:
                                                for elem, data in value.iteritems():
                                                        LinkIndex = PropIndex+"_"+str(elem) + "_" + str(GlobalCount)
                                                        try:
                                                                NavigationPropertyLink = value[elem]
                                                        except:
                                                                NavigationPropertyLink = value['@odata.id']                                                     
                                                        
                                                        tempFlag = False
                                                        temp = ""
                                                        for eachCount in range(0, GlobalCount):
                                                                
                                                                temp = PropIndex + "_" +str(elem) + "_" + str(eachCount)
                                                                if (temp in ComplexTypeLinksDictionary['SubLinks']) and (NavigationPropertyLink in ComplexTypeLinksDictionary[temp+'.Link']):
                                                                        tempFlag = True # Skip duplicate sublink addition
                                                                        break
                                                        if tempFlag:
                                                                continue
                                                        else:
                                                                GlobalCount = GlobalCount + 1
                                                                
                                                        ComplexTypeLinksDictionary['SubLinks'].append(LinkIndex)
                                                        SchemaAlias = NavigationPropertyType[NavigationPropertyType.find('(')+1:NavigationPropertyType.find(')')]
                                                        ComplexTypeLinksDictionary[LinkIndex+'.Schema'] = SchemaAlias

                                                        ComplexTypeLinksDictionary[LinkIndex+'.Link'] = NavigationPropertyLink
                                                        i+=1
                                        else:
                                                PropIndexAppend = PropIndex + "_" + str(GlobalCount)
                                                NavigationPropertyLink = value
                                                try:
                                                        tempFlag = False
                                                        temp = ""
                                                        for eachCount in range(0, GlobalCount):
                                                                if debug:
                                                                    print "GlobalCount::::::::::::::::::::::::::::::::::::::::::::", GlobalCount
                                                                temp = PropIndex + "_" + str(eachCount)
                                                                if (temp in ComplexTypeLinksDictionary['SubLinks']) and (NavigationPropertyLink in ComplexTypeLinksDictionary[temp+'.Link']):
                                                                        tempFlag = True # Skip duplicate sublink addition
                                                                        break
                                                        if tempFlag:
                                                                continue
                                                        else:
                                                                GlobalCount = GlobalCount + 1
                                                except Exception as e:
                                                     if debug > 1: print "Exception has occurred: ", e  
                                                
                                                ComplexTypeLinksDictionary['SubLinks'].append(PropIndex)
                                                
                                                ComplexTypeLinksDictionary[PropIndex+'.Schema'] = NavigationPropertyType
                                                ComplexTypeLinksDictionary[PropIndex+'.Link'] = NavigationPropertyLink          
                                ComplexLinksFlag = True         

                except Exception as e:
                     if debug > 1: print "Exception has occurred: ", e  
        return ComplexLinksFlag
        
        
# Function to generate Random Value for PATCH requests
def getRandomValue(PropertyName, SchemaAlias, soup, propOrigValue, SchemaName):
        valueType = propUpdateValue = propMinValue = propMaxValue = propValuePattern = None
        try:
                propAttr = PropertyDictionary[SchemaName + "-" + PropertyName+'.Attributes']
                if propAttr.has_key('type'):
                        propType = propAttr['type']

                        if propType == 'Edm.Int16' or propType == 'Edm.Int32' or propType == 'Edm.Int64':
                                valueType = 'Int'
                                if PropertyDictionary.has_key(SchemaName + "-" + PropertyName+'.Validation.Minimum'):
                                        propMinValue = int(PropertyDictionary[SchemaName + "-" + PropertyName+'.Validation.Minimum']['int'])
                                if PropertyDictionary.has_key(SchemaName + "-" + PropertyName+'.Validation.Maximum'):
                                        propMaxValue = int(PropertyDictionary[SchemaName + "-" + PropertyName+'.Validation.Maximum']['int'])
                                if propMinValue == None and propMaxValue == None:
                                        propUpdateValue = random.randint(30,200)
                                elif propMinValue == None:
                                        propUpdateValue = random.randint(1, propMaxValue)
                                else:
                                        propMinValue = 30
                                        propMaxValue = 200
                                        propUpdateValue = random.randint(propMinValue, propMaxValue)
                
                        elif propType == 'Edm.String':
                                if PropertyDictionary.has_key(SchemaName + "-" + PropertyName+'.Validation.Pattern'):
                                        propValuePattern = PropertyDictionary[SchemaName + "-" + PropertyName+'.Validation.Pattern']['string']
                                        propUpdateValue = str(propOrigValue)
                                else:
                                        propUpdateValue = ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(7))
                                valueType = 'Str'
                        elif propType == 'Edm.DateTimeOffset':
                                propUpdateValue = strftime("%Y-%m-%d")+"T00:00:00+0000"
                                valueType = 'Date'
                        elif SchemaAlias in propType:
                                enumName = propType.split(".")[-1]
                                validList = getEnumTypeDetails(soup, enumName)
                                if validList:
                                        propUpdateValue = str(random.choice(validList))
                                        try:
                                                propUpdateValue = int(propUpdateValue)
                                                valueType = 'Int'
                                        except:
                                                valueType = 'Str'                                       
                                else:
                                        propUpdateValue = "None"                                
                        elif PropertyName.count(".") == 2:
                                        status, moreSoup = getSchemaDetails("Resource")
                                        validList = getEnumTypeDetails(moreSoup, enumName)
                                        if validList:
                                                propUpdateValue = str(random.choice(validList))
                                                try:
                                                        propUpdateValue = int(propUpdateValue)
                                                        valueType = 'Int'
                                                except:
                                                        valueType = 'Str'                                       
                                        else:
                                                propUpdateValue = "None"
                        elif propType == "Edm.Boolean":
                                if str(propOrigValue).lower() == "true":
                                        propUpdateValue = False
                                else:
                                        propUpdateValue = True
                                valueType = 'Bool'
                else:
                        propUpdateValue = "None"
                        valueType = 'Str'
        except Exception as e:
                     if debug > 1: print "Exception has occurred: ", e  
        return propUpdateValue, valueType

# Function to handle Patch functionality checks for ReadWrite attributes
def checkPropertyPatchCompliance(PropertyList, PatchURI, decoded, soup, headers, SchemaName):
        resultList = dict()
        counters = Counter()
        def propertyUpdate(PropertyName, PatchURI, payload):
                statusCode, status, jsonSchema, headers = callResourceURI('', PatchURI, 'PATCH', payload)
                if not(status):
                        failMessage = "Update Failed - " + str(statusCode)
                        return statusCode, status, failMessage
                time.sleep(5)
                statusCode, status, jsonSchema, headers = callResourceURI('', PatchURI, 'ReGET')
                if not(status):
                        failMessage = "GET after Update Failed - " + str(statusCode)
                        return statusCode, status, failMessage
        
                try:
                        if PropertyName.count(".") == 2:
                                MainAttribute = midAttribute = SubAttribute = propNewValue = ""
                                try:
                                        MainAttribute = PropertyName.split(".")[0]
                                        midAttribute = PropertyName.split(".")[1]
                                        SubAttribute = PropertyName.split(".")[-1]
                                        propNewValue = jsonSchema[MainAttribute][midAttribute][SubAttribute]
                                except:
                                        propNewValue = "Value Not Available"
                                        return statusCode, False, propNewValue
                                        
                        elif PropertyName.count(".") == 1:
                                try:
                                        MainAttribute = PropertyName.split(".")[0]
                                        SubAttribute = PropertyName.split(".")[1]
                                        propNewValue = jsonSchema[MainAttribute][SubAttribute]                                                  
                                except:
                                        propNewValue = "Value Not Available"
                                        return statusCode, False, propNewValue
                        else:
                                propNewValue = jsonSchema[PropertyName] 
                        return statusCode, True, propNewValue
                except Exception:
                        propNewValue = "Value Not Available"                    
                        return statusCode, False, propNewValue
                
        def logPatchResult(PropertyName, status, patchTable, logText, expValue, actValue, WarnCheck = None):
                successMessage = "None"
                if isinstance(expValue, int):
                        expValue = str(expValue)
                if isinstance(actValue, int):
                        actValue = str(actValue)
                if status:
                        counters['pass'] += 1
                        successMessage = "Pass"
                else:
                        if WarnCheck == "Skip":
                                successMessage = "Skip"
                                counters['skip'] += 1
                                counters['total'] -= 1 
                        elif WarnCheck:
                                successMessage = "Warn"
                                counters['warn'] += 1
                        else:
                                successMessage = "Fail"
                                counters['fail'] += 1
                resultList[PropertyName] = (logText,expValue,actValue,successMessage,status)
                                
        for PropertyName in PropertyList:
                        if ':' in PropertyName:
                                PropertyName = PropertyName[PropertyName.find(':')+1:]
                        if 'Oem' in PropertyName:
                                continue
                        counters['total'] += 1
                        propMandatory = False

                        if PropertyDictionary.has_key(SchemaName + "-" + PropertyName+'.OData.Permissions'):
                                propPermissions = PropertyDictionary[SchemaName + "-" + PropertyName+'.OData.Permissions']['enummember']
                        else:
                                propPermissions = ""
                        print "Property Name:", PropertyName, "Permission:", propPermissions
                        if propPermissions == 'OData.Permission/ReadWrite':
                                propAttr = PropertyDictionary[SchemaName + "-" + PropertyName+'.Attributes']
                                if (propAttr.has_key('type')):
                                        propType = propAttr['type']                             
                                if PropertyName in ("Links","Enabled","Locked") or any(s in PropertyName for s in ("UserName","Password","HTTP")):
                                        continue

                                SchemaAlias = soup.find('schema')['namespace'].split(".")[0]
                                propUpdateValue = ""
                                try:
                                        if PropertyName.count(".") == 2:
                                                MainAttribute = midAttribute = SubAttribute = propOrigValue = ""
                                                try:
                                                        MainAttribute = PropertyName.split(".")[0]
                                                        midAttribute = PropertyName.split(".")[1]
                                                        SubAttribute = PropertyName.split(".")[-1]
                                                        propOrigValue = decoded[MainAttribute][midAttribute][SubAttribute]
                                                except:
                                                        propOrigValue = None
                                        elif PropertyName.count(".") == 1:
                                                try:
                                                        MainAttribute = PropertyName.split(".")[0]
                                                        SubAttribute = PropertyName.split(".")[1]
                                                        propOrigValue = decoded[MainAttribute][SubAttribute]                                                    
                                                except:
                                                        propOrigValue = None
                                        else:
                                                try:
                                                        propOrigValue = decoded[PropertyName]                                           
                                                except:
                                                        propOrigValue = None
                                                        
                                except Exception:
                                        propOrigValue = "Value Not Available"
                                
                                if propOrigValue == None:
                                        print "No Property available for patch: ", PropertyName
                                        continue
                                        
                                breakloop = 1   
                                while True:
                                        breakloop = breakloop + 1
                                        propUpdateValue, valueType = getRandomValue(PropertyName, SchemaAlias, soup, propOrigValue, SchemaName)
                                        if not(str(propUpdateValue).lower() == str(propOrigValue).lower()):
                                                break
                                        if propUpdateValue == "None" or propUpdateValue == "" or propUpdateValue == True or propUpdateValue == False or propUpdateValue == "Disabled" or propUpdateValue == "Enabled":
                                                break
                                        if breakloop >= 5:
                                                break
                                                
                                if propUpdateValue == "None":
                                        print "No patch support on: ", PropertyName
                                        continue
                                        
                                patchTable = htmlTable.tr.td.table(border='1', style="font-family: calibri; width: 100%")
                                header = patchTable.tr(style="background-color: #FFFFA3")
                                header.th("PATCH Compliance for Property: "+PropertyName, style="width: 40%")
                                header.th("Expected Value", style="width: 20%")
                                header.th("Actual Value", style="width: 20%")
                                header.th("Result", style="width: 20%")         
                                
                                if getOnly:
                                        print "NonGet Property Skipped"
                                        logPatchResult(False, patchTable, "Skipped", "-", "-", WarnCheck="Skip")
                                        continue
                                else:
                                        if valueType == 'Int':
                                                propMinValue = propMaxValue = None
                                                if PropertyDictionary.has_key(SchemaName + "-" + PropertyName+'.Validation.Minimum'):
                                                        propMinValue = int(PropertyDictionary[SchemaName + "-" + PropertyName+'.Validation.Minimum']['int'])
                                                        if PropertyName.count(".") == 1:
                                                                MainAttribute = PropertyName.split(".")[0]
                                                                SubAttribute = PropertyName.split(".")[1]                                                       
                                                                payload = "{\""+ MainAttribute +"\":{\""+SubAttribute+"\":"+str(propMinValue)+"}}"
                                                                
                                                        else:
                                                                payload = "{\""+PropertyName+"\":"+str(propMinValue)+"}"
                                                                
                                                        statusCode, status, retValue = propertyUpdate(PropertyName, PatchURI, payload)
                                                        
                                                        if retValue == propMinValue:
                                                                logPatchResult(PropertyName, True, patchTable, "Valid Update Value", propMinValue, retValue)
                                                        elif str(statusCode) in ["200", "204", "400", "405"]:
                                                                logPatchResult(PropertyName, False, patchTable, "Valid Update Value", propMinValue, retValue, WarnCheck = True)
                                                        else:
                                                                logPatchResult(PropertyName, False, patchTable, "Valid Update Value", propMinValue, retValue)                                                                 

                                                if PropertyDictionary.has_key(SchemaName + "-" + PropertyName+'.Validation.Maximum'):
                                                        propMaxValue = int(PropertyDictionary[SchemaName + "-" + PropertyName+'.Validation.Maximum']['int'])
                                                        if PropertyName.count(".") == 1:
                                                                MainAttribute = PropertyName.split(".")[0]
                                                                SubAttribute = PropertyName.split(".")[1]                                                       
                                                                payload = "{\""+ MainAttribute +"\":{\""+SubAttribute+"\":"+str(propMaxValue)+"}}"
                                                        else:
                                                                payload = "{\""+PropertyName+"\":"+str(propMaxValue)+"}"
                                                                
                                                        statusCode, status, retValue = propertyUpdate(PropertyName, PatchURI, payload)

                                                        if retValue == propMaxValue:
                                                                logPatchResult(PropertyName, True, patchTable, "Valid Update Value", propMaxValue, retValue)
                                                        elif str(statusCode) in ["200", "204", "400", "405"]:
                                                                logPatchResult(PropertyName, False, patchTable, "Valid Update Value", propMaxValue, retValue, WarnCheck = True)
                                                        else:
                                                                logPatchResult(PropertyName, False, patchTable, "Valid Update Value", propMaxValue, retValue)
                                        else:                        
                                                if PropertyName.count(".") == 1:
                                                        MainAttribute = PropertyName.split(".")[0]
                                                        SubAttribute = PropertyName.split(".")[1]                                                       
                                                        payload = "{\""+ MainAttribute +"\":{\""+SubAttribute+"\":\""+str(propUpdateValue)+"\"}}"
                                                        payloadOriginalValue = "{\""+ MainAttribute +"\":{\""+SubAttribute+"\":\""+str(propOrigValue)+"\"}}"
                                                else:
                                                        payload = "{\""+PropertyName+"\":\""+str(propUpdateValue)+"\"}"
                                                        payloadOriginalValue = "{\""+PropertyName+"\":\""+str(propOrigValue)+"\"}"
                                                        
                                                statusCode, status, retValue = propertyUpdate(PropertyName, PatchURI, payload)

                                                if str(retValue).lower() == str(propUpdateValue).lower():
                                                        logPatchResult(PropertyName, True, patchTable, "Valid Update Value", propUpdateValue, retValue)
                                                elif str(statusCode) in ["200", "204", "400", "405"]:
                                                        logPatchResult(PropertyName, False, patchTable, "Valid Update Value", propUpdateValue, retValue, WarnCheck = True)
                                                else:
                                                        logPatchResult(PropertyName, False, patchTable, "Valid Update Value", propUpdateValue, retValue)
                                        
                                                statusCode, status, jsonSchema, headers = callResourceURI('', PatchURI, 'PATCH', payload=payloadOriginalValue, mute=True)

                        else:
                                        continue
        print resultList
        print counters
        return resultList, counters

#Check all the GET property comparison with Schema files                
def checkPropertyType(PropertyName, PropertyDictionary, propValue, propType, optionalFlag, propMandatory, soup, SchemaName):

        if propType == 'Edm.Boolean':
                if str(propValue).lower() == "true" or str(propValue).lower() == "false":
                        generateLog(PropertyName, "Boolean Value", str(propValue), propMandatory)
                else:
                        generateLog(PropertyName, "Boolean Value", str(propValue), propMandatory, logPass = False)
        elif propType == 'Edm.String':
                if PropertyDictionary.has_key(SchemaName + "-" + PropertyName+'.Validation.Pattern'):
                        propValuePattern = PropertyDictionary[SchemaName + "-" + PropertyName+'.Validation.Pattern']['string']
                        if "\\" in propValuePattern:
                                propValuePattern = propValuePattern.replace("\\\\", "\\")
                        if (re.match(propValuePattern, propValue) == None):
                                generateLog(PropertyName, "String Value (Pattern: "+propValuePattern+")", propValue, propMandatory, logPass = False)
                        else:
                                generateLog(PropertyName, "String Value (Pattern: "+propValuePattern+")", propValue, propMandatory)
                elif (len(propValue) >= 1 or (optionalFlag and len(propValue) == 0)):
                        generateLog(PropertyName, "String Value", propValue, propMandatory)
                else:
                        generateLog(PropertyName, "String Value", propValue, propMandatory, logPass = False)
        elif propType == 'Edm.DateTimeOffset':
                temp = False
                try: 
                        propValueCheck = propValue[:19]
                        d1 = strptime(propValueCheck, "%Y-%m-%dT%H:%M:%S" )
                        temp = True
                except Exception as e:
                     if debug > 1: print "Exception has occurred: ", e  
                try: 
                        propValueCheck = propValue.split("T")[0]
                        d1 = strptime(propValueCheck, "%Y-%m-%d" )
                        temp = True
                except Exception as e:
                     if debug > 1: print "Exception has occurred: ", e  
                try:
                        propValueCheck = propValue.split(" ")[0]
                        d1 = strptime(propValueCheck, "%Y-%m-%d" )
                        temp = True
                except Exception as e:
                     if debug > 1: print "Exception has occurred: ", e  
                if (temp):
                        generateLog(PropertyName, "DateTime Value", propValue, propMandatory)
                else:
                        generateLog(PropertyName, "DateTime Value", propValue, propMandatory, logPass = False)
        elif propType == 'Edm.Int16' or propType == 'Edm.Int32' or propType == 'Edm.Int64':
                if isinstance(propValue, int):
                        logText = "Integer Value"
                        if PropertyDictionary.has_key(SchemaName + "-" + PropertyName+'.Validation.Minimum'):
                                propMinValue = int(PropertyDictionary[SchemaName + "-" + PropertyName+'.Validation.Minimum']['int'])
                                if propValue >= propMinValue:
                                        logText += " Range: "+str(propMinValue)
                                else:
                                        generateLog("Check failed for property " + PropertyName, "Minimum Boundary = " + str(propMinValue), str(propValue), propMandatory, logPass = False)
                                        return
                        if PropertyDictionary.has_key(SchemaName + "-"  +PropertyName+'.Validation.Maximum'):
                                propMaxValue = int(PropertyDictionary[SchemaName + "-" + PropertyName+'.Validation.Maximum']['int'])
                                if propValue <= propMaxValue:
                                        logText += " - "+str(propMaxValue)
                                else:
                                        generateLog("Check failed for property " + PropertyName, "Maximum Boundary = " + str(propMaxValue), str(propValue), propMandatory, logPass = False)
                                        return
                        generateLog(PropertyName, logText, str(propValue), propMandatory)
                else:
                        generateLog(PropertyName, logText, str(propValue), propMandatory, logPass = False)
                        
        elif propType == 'Edm.Guid':
                propValuePattern = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
                if (re.match(propValuePattern, propValue) == None):
                        generateLog(PropertyName, "String Value (Pattern: "+propValuePattern+")", propValue, propMandatory, logPass = False)
                else:
                        generateLog(PropertyName, "String Value (Pattern: "+propValuePattern+")", propValue, propMandatory)
        else:
                validList = temp = ""
                templist = []
                if debug:
                    print "Inside Complex Data Type"
                if propType.__contains__("Resource"):
                        status, soup = getSchemaDetails("Resource")
                        SchemaAlias = soup.find('schema')['namespace'].split(".")[0]
                else:
                        SchemaAlias = soup.find('schema')['namespace'].split(".")[0]
                        
                if SchemaAlias in propType:
                        if 'Collection(' in propType:
                                propType = propType.replace('Collection(', "")
                                propType = propType.replace(')', "")
                        
                        validList = getEnumTypeDetails(soup, propType.split(".")[-1])   

                        if not validList:
                                print 'Special verification for Complex Data Types defined in schema', SchemaAlias+':', propType
                                generateLog(PropertyName, "Complex Data Type", propType, propMandatory)                         
                        else:
                                flag =True
                                if type(propValue) is list:
                                        temp = str(propValue)
                                        temp = temp.replace("[","")
                                        temp = temp.replace("]","")
                                        temp = temp.replace("u'","")
                                        temp = temp.replace("'","")
                                        temp = temp.replace(", ",",")
                                        temp = temp.replace("\"","")
                                        templist = temp.split(",")
                                        templist = list(templist)
                                        for eachValue in templist:
                                                if eachValue.lower() in [element.lower() for element in validList]:
                                                        print "Covered"
                                                else:
                                                        flag = False
                                        if flag:
                                                print 'Property present in List', SchemaAlias+':', propValue
                                                generateLog(PropertyName, "Value Matched", str(propValue), propMandatory)
                                        else:
                                                generateLog(PropertyName, "Value Not Matched", str(propValue), propMandatory, logPass = False)
                                                        
                                elif propValue.lower() in [element.lower() for element in validList]:
                                        print 'Property present in List', SchemaAlias+':', propValue
                                        generateLog(PropertyName, "Value Matched", propValue, propMandatory)    
                                else:
                                        generateLog(PropertyName, "Value Not Matched", propValue, propMandatory, logPass = False)

gencount = Counter()
# Common function to handle rerport generation in HTML/XML format
def generateLog(logText, expValue, actValue, propMandatory = False, logPass = True, incrementCounter = True, header = False, spacer = False, summaryLog = False):
        global gencount
        if logPass:
                if (actValue == None or actValue == ""):
                        gencount['skip'] += 1
                        incrementCounter = False
                if incrementCounter:
                        gencount['pass'] += 1
                        countPassProp+=1
                        if propMandatory:
                                countPassMandatoryProp+=1
        else:
            if incrementCounter:
                    gencount['fail'] += 1
                    if propMandatory:
                            countFailMandatoryProp+=1
        print gencount
        return
        global countTotSchemaProp, countPassSchemaProp, countFailSchemaProp, countSkipSchemaProp, countWarnSchemaProp
        global countTotMandatoryProp, countPassMandatoryProp, countFailMandatoryProp, countWarnMandatoryProp
        global countTotProp, countPassProp, countFailProp, countSkipProp, countWarnProp
        global propTable

        if summaryLog:
                logTable = htmlSumTable
        else:
                logTable = htmlTable

        if spacer:
                logTable.tr.td(style = "height: 30")
                return

        if (expValue == None and actValue == None): # Add Information steps to log
                print 80*'*'
                print logText
                if header:
                        rowData = logTable.tr(align = "center", style = "font-size: 21px; background-color: #E6E6F0")
                else:
                        rowData = logTable.tr(align = "center", style = "font-size: 18px; background-color: #FFE0E0")

                if logText.__contains__("Compliance Check"):
                        clickLink = rowData.td.a(id = logText, href= SummaryLogFile)
                        clickLink(logText)                      
                else:
                        rowData.td(logText)
                
                return

        if propMandatory:
                logManOpt = 'Mandatory'
        else:
                logManOpt = 'Optional'
        if logPass:
                print 'PASS:', 'Compliance successful for', logText, '|| Value matches compliance:', actValue
                propRow = propTable.tr(style="color: #006B24")
                propRow.td(logText)
                propRow.td(logManOpt)
                if expValue == None:
                        propRow.td("No Value")
                else:
                        propRow.td(expValue)
                if (actValue == None or actValue == ""):
                        propRow.td("No Value Returned")
                        propRow.td("SKIP", align = "center")
                        counters['skip'] += 1
                        counters['total'] -= 1 
                        incrementCounter = False
                else:
                        propRow.td(actValue)
                        propRow.td("PASS", align = "center")
                if incrementCounter:
                        countPassProp+=1
                        if propMandatory:
                                countPassMandatoryProp+=1
        else:
                print 'FAIL:', 'Compliance unsuccessful for', logText, '|| Expected:', expValue, '|| Actual:', actValue
                propRow = propTable.tr(style="color: #ff0000")
                propRow.td(logText)
                propRow.td(logManOpt)
                if expValue == None:
                        propRow.td("No Value")
                else:
                        propRow.td(expValue)
                if (actValue == None or actValue == ""):
                        propRow.td("No Value Returned")
                else:
                        propRow.td(actValue)
                propRow.td("FAIL", align = "center")

# Common module to handle tabular reporting in HTML
def insertResultTable():
        global propTable
        propTable = htmlTable.tr.td.table(border='1', style="font-family: calibri; width: 100%")
        header = propTable.tr(style="background-color: #FFFFA3")
        header.th("Property Name", style="width: 40%")
        header.th("Type", style="width: 9%")
        header.th("Expected Value", style="width: 17%")
        header.th("Actual Value", style="width: 17%")
        header.th("Result", style="width: 17%")
        return propTable

# Function to traverse thorough all the pages of service
def corelogic(ResourceName, SchemaURI):

          
        counters = Counter()
        status, SchemaAlias = getMappedSchema(ResourceName, rootSoup)
        ComplexLinksFlag = False
        linkvar = ""
        ResourceURIlink2 = "ServiceRoot -> " + ResourceName
        if status:
                print SchemaAlias

                status, schemaSoup = getSchemaDetails(SchemaAlias)              
                if not(status):
                        return None     # Continue check of next schema         
                EntityName, PropertyList = getEntityTypeDetails(schemaSoup, SchemaAlias)
                SerialNumber = SerialNumber + 1
                linkvar = "Compliance Check for Schema: "+EntityName + "-" + str(SerialNumber)
                generateLog(linkvar, None, None)
                
                propTable = insertResultTable()
                statusCode, status, jsonSchema, headers = callResourceURI(ResourceName, SchemaURI, 'GET')               
                if status:
                                
                        PropertyDictionary = {}
                        getPropertyDetails(schemaSoup, PropertyList, SchemaAlias)
                        propTable = insertResultTable()
                        SchemaName = SchemaAlias.split(".")[-1]
                        compliance, counts = checkPropertyCompliance(PropertyList, jsonSchema, schemaSoup, SchemaName)
                        patchComplaince, patchCounts = checkPropertyPatchCompliance(PropertyList, SchemaURI, jsonSchema, schemaSoup, headers, SchemaName)
                        ComplexLinksFlag = getChildLinks(PropertyList, jsonSchema, schemaSoup)
                else:
                        print 80*'*'
                        if debug:
                            print schemaSoup
                        print 80*'*'
                        
                generateLog("Properties checked for Schema %s: %s || Pass: %s || Fail: %s || Warning: %s " %(SchemaAlias, countTotProp-countTotSchemaProp, countPassProp-countPassSchemaProp, countFailProp-countFailSchemaProp, countWarnProp-countWarnSchemaProp), None, None)
                propRow = summaryLogTable.tr(align = "center")
                propRow.td(str(SerialNumber))           
                propRow.td(ResourceURIlink2, align = "left")
                propRow.td(SchemaURI, align = "left")
                propRow.td(str(countPassProp-countPassSchemaProp))
                propRow.td(str(countFailProp-countFailSchemaProp))
                propRow.td(str(countSkipProp-countSkipSchemaProp))
                propRow.td(str(countWarnProp-countWarnSchemaProp))
                clickLink = propRow.td.a(href= HTMLLogFile + "#" + linkvar)
                clickLink("Click")
                
                oldSchemaAlias = ""
                ResourceURIlink3 = ""
                while ComplexLinksFlag: # Go into loop only if SubLinks have been found
                        ResourceURIlink2 = ResourceURIlink2
                        SubLinks = ComplexTypeLinksDictionary['SubLinks'][ComplexLinksIndex:]
                        ComplexLinksFlag = False        # Reset the Flag to stop looping
                        for elem in SubLinks:
                                ComplexLinksIndex+=1    # Track the Index counter
                                #if elem in ComplexTypeLinksDictionary['SubLinks'][:ComplexLinksIndex-1]:
                                #       continue
                                countTotSchemaProp = countTotProp
                                countPassSchemaProp = countPassProp
                                countFailSchemaProp = countFailProp
                                countSkipSchemaProp = countSkipProp
                                countWarnSchemaProp = countWarnProp
                                SchemaAlias = ComplexTypeLinksDictionary[elem+'.Schema']
                                subLinkURI = ComplexTypeLinksDictionary[elem+'.Link']

                                if subLinkURI.strip().lower() in AllLinks:
                                        continue
                                else:
                                        AllLinks.append(subLinkURI.strip().lower())
                                
                                generateLog(None, None, None, spacer = True)
                                generateLog(None, None, None, spacer = True)

                                status, schemaSoup = getSchemaDetails(SchemaAlias)
                                if not(status):
                                        continue        # Continue check of next schema 
                        
                                EntityName, PropertyList = getEntityTypeDetails(schemaSoup, SchemaAlias)
                                
                                        
                                SerialNumber = SerialNumber + 1
                                linkvar = "Compliance Check for Sub-Link Schema: "+EntityName + "-" + str(SerialNumber)
                                generateLog(linkvar, None, None)
                                
                                propTable = insertResultTable()                         
                                statusCode, status, jsonSchema, headers = callResourceURI(SchemaAlias, subLinkURI, 'GET')
                                
                                if status:
                                        PropertyDictionary = {}
                                        getPropertyDetails(schemaSoup, PropertyList, SchemaAlias)
                                        propTable = insertResultTable()
                                        SchemaName = SchemaAlias.split(".")[-1]
                                        compliance, counts = checkPropertyCompliance(PropertyList, jsonSchema, schemaSoup, SchemaName)
                                        patchComplaince, patchCounts = checkPropertyPatchCompliance(PropertyList, subLinkURI, jsonSchema, schemaSoup, headers, SchemaName)
                                        #checkPropertyPostCompliance(PropertyList, subLinkURI, jsonSchema, schemaSoup)
                                        ComplexLinksFlag = getChildLinks(PropertyList, jsonSchema, schemaSoup)

                                else:
                                        print 80*'*'
                                        if debug:
                                            print schemaSoup
                                        print 80*'*'
                                
                                ResourceURIlink3 = ResourceURIlink2 + " -> " + SchemaAlias.split(".")[0]
                                        
                                generateLog("Properties checked for Sub-Link Schema %s: %s || Pass: %s || Fail: %s || Warning: %s " %(SchemaAlias, countTotProp-countTotSchemaProp, countPassProp-countPassSchemaProp, countFailProp-countFailSchemaProp, countWarnProp-countWarnSchemaProp), None, None)
                                propRow = summaryLogTable.tr(align = "center")

                                propRow.td(str(SerialNumber))
                                propRow.td(ResourceURIlink3, align = "left")
                                propRow.td(subLinkURI, align = "left")
                                propRow.td(str(countPassProp-countPassSchemaProp))
                                propRow.td(str(countFailProp-countFailSchemaProp))
                                propRow.td(str(countSkipProp-countSkipSchemaProp))
                                propRow.td(str(countWarnProp-countWarnSchemaProp))
                                clickLink = propRow.td.a(href= HTMLLogFile + "#" + linkvar)
                                clickLink("Click")
                        oldSchemaAlias = SchemaAlias.split(".")[0]      
        else:
                print 80*'*'
                print SchemaAlias
                print 80*'*'

allLinks = set()
def validateURI (URI, uriName=''):
    print "***", uriName, URI
    counts = Counter()
    print uriName, URI
    
    success, jsonData = callResourceURI(uriName, URI)
    
    if not success:
        print "Get URI failed."
        counts['fail'] += 1
        return False, counts
    
    counts['pass'] += 1

    SchemaFullType = jsonData['@odata.type']
    SchemaType = getType(SchemaFullType)
    SchemaNamespace = getNamespace(SchemaFullType)

    success, SchemaSoup = getSchemaDetails(SchemaType)
    
    if not success:
        success, SchemaSoup = getSchemaDetails(SchemaNamespace)
        if not success:
            success, SchemaSoup = getSchemaDetails(uriName)
        if not success: 
            print "No schema for", SchemaFullType, uriName
            counts['fail'] += 1
            return False, counts
    
    propertyList = getEntityTypeDetails(SchemaSoup,SchemaFullType)
    
    links = getAllLinks(jsonData)
    
    print links

    
    for key in propertyList:
        print key

    propertyDict = getPropertyDetails(SchemaSoup, propertyList, SchemaFullType)
   
    # messages, checkCounts = checkPropertyCompliance(properties, PropertyDictionary, jsonData, SchemaSoup, SchemaName)
    
    # counts.update(checkCounts)

    return True, counts
    
    for linkName in links:
        print uriName, '->', linkName
        if links[linkName] in allLinks:
            continue
        allLinks.add(links[linkName])
        success, mappedName = getMappedSchema(linkName,SchemaSoup)
        if not success:
            print "mappedSchema not found", linkName
            pass
        success, linkCounts = validateURI('_',links[linkName])
        if success:
            counts.update(linkCounts)

    return True, counts

##########################################################################
######################          Script starts here              ######################
##########################################################################

if __name__ == '__main__':
    # Rewrite here
    status_code = 1
    success, counts = validateURI ('/redfish/v1')
    
    if not success:
        print "Validation has failed."
        sys.exit(1)    
   
    print counts
    sys.exit(0)

    # Initialize Log files for HTML report
    HTMLLogFile = strftime("ComplianceTestDetailedResult_%m_%d_%Y_%H%M%S.html")
    SummaryLogFile = strftime("ComplianceTestSummary_%m_%d_%Y_%H%M%S.html")
    logHTML = HTML('html')
    logSummary = HTML('html')
    loghead = logHTML.head
    logbody = logHTML.body
    loghead.title('Compliance Log')
    logSumhead = logSummary.head
    logSumbody = logSummary.body
    logSumhead.title('Compliance Test Summary')
    startTime = DT.now()

    htmlTable = logbody.table(border='1', style="font-family: calibri; width: 100%; font-size: 14px")
    generateLog("#####         Starting Redfish Compliance Test || System: %s as User: %s     #####" %(ConfigURI, User), None, None, header = True)
    htmlSumTable = logSumbody.table(border='1', style="font-family: calibri; width: 80%; font-size: 14px", align = "center")
    generateLog("#####         Redfish Compliance Test Report         #####", None, None, header = True, summaryLog = True)
    generateLog("System: %s" %ConfigURI[ConfigURI.find("//")+2:], None, None, summaryLog = True)
    generateLog("User: %s" %(User), None, None, summaryLog = True)
    generateLog("Execution Date: %s" %strftime("%m/%d/%Y %H:%M:%S"), None, None, summaryLog = True)
    generateLog(None, None, None, spacer = True, summaryLog = True)

    summaryLogTable = htmlSumTable.tr.td.table(border='1', style="width: 100%")
    header = summaryLogTable.tr(style="background-color: #FFFFA3")
    header.th("Serial No", style="width: 5%")
    header.th("Resource Name", style="width: 30%")
    header.th("Resource URI", style="width: 40%")
    header.th("Passed", style="width: 5%")
    header.th("Failed", style="width: 5%")
    header.th("Skipped", style="width: 5%")
    header.th("Warning", style="width: 5%")
    header.th("Details", style="width: 5%")
    linkvar = "Compliance Check for Root Schema" + "-" + str(SerialNumber)
    print 80*'*'
    generateLog(None, None, None, spacer = True)
    propTable = insertResultTable()
    generateLog(linkvar, None, None)

    # Retrieve output of ServiceRoot URI
    status, jsonData = getRootURI()                                                        

    ResourceURIlink1 = "ServiceRoot"
    if status:
            # Check compliance for ServiceRoot
            status, schemaSoup = getSchemaDetails('ServiceRoot')

            Name, PropertyList = getEntityTypeDetails(schemaSoup, 'ServiceRoot')
            
            PropertyDictionary = {}
            ComplexLinksFlag = False

            getPropertyDetails(schemaSoup, PropertyList, 'ServiceRoot')
            
            propTable = insertResultTable()
            checkPropertyCompliance(PropertyList, jsonData, schemaSoup, 'ServiceRoot')
            # Report log statistics for ServiceRoot schema
            generateLog("Properties checked: %s || Pass: %s || Fail: %s || Warning: %s " %(countTotProp, countPassProp, countFailProp, countWarnProp), None, None)
            propRow = summaryLogTable.tr(align = "center")
            propRow.td(str(SerialNumber))
            propRow.td(ResourceURIlink1, align = "left")
            propRow.td("/redfish/v1", align = "left")
            propRow.td(str(countPassProp-countPassSchemaProp))
            propRow.td(str(countFailProp-countFailSchemaProp))
            propRow.td(str(countSkipProp-countSkipSchemaProp))
            propRow.td(str(countWarnProp-countWarnSchemaProp))
            clickLink = propRow.td.a(href= HTMLLogFile + "#" + linkvar)
            clickLink("Click")

            rootSoup = schemaSoup
            generateLog(None, None, None, spacer = True)
            propTable = htmlTable.tr.td.table(border='1', style="width: 100%")
            header = propTable.tr(style="background-color: #FFFFA3")
            header.th("Resource Name", style="width: 30%")
            header.th("URI", style="width: 70%")
            
            ### Executing all the links on root URI         
            for elem, value in jsonData.iteritems():
                            if type(value) is dict:
                                    for eachkey, eachvalue in value.iteritems():
                                                    if eachkey == '@odata.id':
                                                            ResourceName = elem
                                                            SchemaURI = jsonData[ResourceName][eachkey]
                                                            corelogic(ResourceName, SchemaURI)
                                                            propRow = propTable.tr()
                                                            propRow.td(ResourceName)
                                                            propRow.td(SchemaURI)
                                                            
                                                    elif jsonData[elem][eachkey].has_key('@odata.id'):
                                                            ResourceName = eachkey
                                                            SchemaURI = jsonData[elem][ResourceName]['@odata.id']
                                                            corelogic(ResourceName, SchemaURI)
                                                            propRow = propTable.tr()
                                                            propRow.td(ResourceName)
                                                            propRow.td(SchemaURI)
                                                    else:
                                                            pass
                            else:
                                    pass
                            
            
            generateLog(None, None, None, spacer = True, summaryLog = True)
            summaryLogTable = htmlSumTable.tr.td.table(border='1', style="width: 100%")
            header = summaryLogTable.tr(style="background-color: #FFFFA3")
            header.th("Compliance Test Summary", style="width: 40%")
            header.th("Passed", style="width: 15%")
            header.th("Failed", style="width: 15%")
            header.th("Skipped", style="width: 15%")
            header.th("Warning", style="width: 15%")

            summaryRow = summaryLogTable.tr(align = "center")
            summaryRow.td("Mandatory Properties", align = "left")
            summaryRow.td(str(countPassMandatoryProp))
            summaryRow.td(str(countFailMandatoryProp))
            summaryRow.td('0')
            summaryRow.td(str(countWarnMandatoryProp))
            
            summaryRow = summaryLogTable.tr(align = "center")
            summaryRow.td("Optional Properties", align = "left")
            summaryRow.td(str(countPassProp - countPassMandatoryProp))
            summaryRow.td(str(countFailProp - countFailMandatoryProp))
            summaryRow.td(str(countSkipProp))
            summaryRow.td(str(countWarnProp - countWarnMandatoryProp))
            summaryRow = summaryLogTable.tr(align = "center", style = "background-color: #E6E6F0")
            summaryRow.td("Total Properties", align = "left")
            summaryRow.td(str(countPassProp))
            summaryRow.td(str(countFailProp))
            summaryRow.td(str(countSkipProp))
            summaryRow.td(str(countWarnProp))

            generateLog(None, None, None, spacer = True, summaryLog = True)
            if (countFailProp > 0 or countFailMandatoryProp > 0):
                    logComment = "Compliance Test Result: FAIL"
                    summaryRow = htmlSumTable.tr(align = "center", style = "font-size: 18px; background-color: #E6E6F0; color: #ff0000")
            elif (countPassProp > 0):
                    logComment = "Compliance Test Result: PASS"
                    summaryRow = htmlSumTable.tr(align = "center", style = "font-size: 18px; background-color: #E6E6F0; color: #006B24")
                    status_code = 0
            else:
                    logComment = "Compliance Test Result: INCOMPLETE"
            summaryRow.td(logComment)
    else:
            print "Compliance FAIL for ServiceRoot. Error:", jsonData

    endTime = DT.now()
    execTime = endTime - startTime
    timeLog = htmlSumTable.tr(align = "left", style="font-size: 11px")
    timeLog.td("Execution Time: " + str(execTime))

    timeLog = htmlSumTable.tr(align = "left", style="font-size: 11px")
    timeLog.td("* Warning: " + str("Value which we are trying to configure is not getting set using compliance tool are may be due to external dependency."))

    # Save HTML Log Files
    filehandle = open(os.path.join('.', 'logs', HTMLLogFile), "w")
    filehandle.write(str(logHTML))
    filehandle.close()
    filehandle = open(os.path.join('.', 'logs', SummaryLogFile), "w")
    filehandle.write(str(logSummary))
    filehandle.close()

    generateLog("#####        End of Compliance Check. Please refer logs.    #####", None, None)
    print 80*'*'

