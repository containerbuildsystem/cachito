/*
This script configures Nexus so that Cachito can stage JavaScript content for the Cachito request.

This script creates an Rubygems proxy repository that proxies requests to the cachito-rubygems Rubygems repository group.

No permissions are configured since it is expected that Cachito's Nexus service account has access to use all Rubygems
repositories managed by the Nexus instance.
 */
import groovy.json.JsonSlurper
import groovy.transform.Field
import org.slf4j.Logger
import org.slf4j.LoggerFactory
import org.sonatype.nexus.repository.config.Configuration


// Scope logger to the script using @Field
@Field final Logger logger = LoggerFactory.getLogger('cachito');


def createRequestRepo(String repositoryName, String rubygemsProxyUrl, String httpUsername, String httpPassword) {
    /*
        Here are some of the properties that can be set:
          repositoryName: name,
          recipeName: recipeName,
          online: true,
          attributes: [
            httpclient: [
              authentication: <See Below>,
              autoBlock: <Boolean>,
              blocked: <Boolean>,
              connection: https://bit.ly/39hLQBZ
            ],
            proxy: [
              remoteUrl: <String>,
              contentMaxAge: <Integer>,
              metadataMaxAge: <Integer>
            ],
            negativeCache: [
              enabled: <Boolean>,
              timeToLive: <Integer>
            ],
            storage: [
              blobStoreName: <String>,
              strictContentTypeValidation: <Boolean>
            ]
          ]

        httpclient.authentication (select from one of the following types):
        - username: https://bit.ly/2WMAxPB
        - ntlm: https://bit.ly/3bC87w7
        - bearerToken: https://bit.ly/2QL4egm

        For example:
          def authentication = repo_config.attributes('httpclient').child('authentication')
          authentication.set('type', 'username')
          authentication.set('username', 'cachito')
          authentication.set('password', 'secret_password')
     */
    // This object is provided by Nexus when the script is executed
    def repositoryManager = repository.repositoryManager

    Configuration repoConfig
    Boolean exists = repositoryManager.exists(repositoryName)
    if(exists) {
        logger.info("Modifying the repository ${repositoryName}")
        repoConfig = repositoryManager.get(repositoryName).configuration
    }
    else {
        logger.info("Creating the repository ${repositoryName}")
        repoConfig = repositoryManager.newConfiguration()
        repoConfig.repositoryName = repositoryName
        repoConfig.recipeName = 'rubygems-proxy'
        repoConfig.attributes('storage').set('blobStoreName', 'cachito-rubygems')
    }

    repoConfig.online = true
    repoConfig.attributes('storage').set('strictContentTypeValidation', true)
    // This URL is from the perspective of the Nexus instance
    repoConfig.attributes('proxy').set('remoteUrl', rubygemsProxyUrl)

    def httpclient = repoConfig.attributes('httpclient')
    // Don't block connections to cachito-rubygems if it is down
    httpclient.set('autoBlock', false)
    // Don't block connections to cachito-rubygems
    httpclient.set('blocked', false)

    def connection = httpclient.child('connection')
    if (rubygemsProxyUrl.startsWith('https://')) {
      // Always assume the trust store can be used if https is used
      connection.set('useTrustStore', true)
    }
    else {
      connection.set('useTrustStore', false)
    }

    // This is the authentication required for this proxy to access the cachito-rubygems Rubygems repository group
    if (httpUsername && httpPassword) {
      def authentication = httpclient.child('authentication')
      authentication.set('type', 'username')
      authentication.set('username', httpUsername)
      authentication.set('password', httpPassword)
    }
    else {
      httpclient.set('authentication', null)
    }

    if(exists) {
        repositoryManager.update(repoConfig)
    }
    else {
        repositoryManager.create(repoConfig)
    }
}


// http_username and http_password are the credentials to connect to the cachito-rubygems Rubygems repository group
request = new JsonSlurper().parseText(args)
['rubygems_repository_name', 'rubygems_proxy_url'].each { param ->
    assert request.get(param): "The ${param} parameter is required"
}

// http_username and http_password will be null if the Rubygems proxy repo does not need authentication
createRequestRepo(request.rubygems_repository_name, request.rubygems_proxy_url, request.http_username, request.http_password)

return 'The repository was created successfully'
